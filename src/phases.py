from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .checkpoint import Checkpoint
from .config import Settings
from .csv_reader import (
    CPF_ZEROS,
    nome_valido,
    normalizar_cpf,
    username_from_cpf,
    validar_cpf,
    validar_email,
)
from .lotes import LoteManager
from .password import gerar_senha
from .rocketchat import RocketChatClient, RocketChatError, sleep_ms
from .safety import assert_safe_to_delete, is_protected_username

logger = logging.getLogger(__name__)


@dataclass
class FaseContadores:
    lidos: int = 0
    pulados: int = 0
    criados: int = 0
    existentes: int = 0
    emails: int = 0
    falhas: int = 0
    mensagens: list[str] = field(default_factory=list)


@dataclass
class JobState:
    running: bool = False
    fase: str | None = None
    cancel_requested: bool = False
    contadores: FaseContadores | None = None
    erro: str | None = None
    log_lines: list[str] = field(default_factory=list)

    def push(self, msg: str) -> None:
        self.log_lines.append(msg)
        if len(self.log_lines) > 200:
            self.log_lines = self.log_lines[-200:]
        logger.info(msg)


# Estado global do job (painel web)
_job = JobState()
_job_lock = threading.Lock()


def get_job_state() -> dict[str, Any]:
    with _job_lock:
        cont = _job.contadores
        return {
            "running": _job.running,
            "fase": _job.fase,
            "cancel_requested": _job.cancel_requested,
            "erro": _job.erro,
            "log_lines": list(_job.log_lines[-50:]),
            "contadores": None
            if cont is None
            else {
                "lidos": cont.lidos,
                "pulados": cont.pulados,
                "criados": cont.criados,
                "existentes": cont.existentes,
                "emails": cont.emails,
                "falhas": cont.falhas,
            },
        }


class PhaseService:
    """Fase 1: criar usuários sem e-mail. Fase 2: forgotPassword. Delete seguro."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.checkpoint = Checkpoint(settings.checkpoint_path)
        self.lotes = LoteManager(settings.data_dir, settings.checkpoint_path)
        self.rc = RocketChatClient(
            settings.rc_base_url,
            settings.rc_admin_user,
            settings.rc_admin_password,
        )
        self.erros_path = settings.data_dir / "erros.csv"
        self.sucesso_path = settings.data_dir / "sucesso.csv"
        self._room_id: str | None = None
        self._room_type: str | None = None
        self._room_name: str | None = None
        self._ensure_report_headers()

    def _ensure_snas_room(self, log: Callable[[str], None] | None = None) -> None:
        """Garante que o canal padrão é SNAS (único canal de entrada dos usuários)."""
        if self._room_id:
            return
        room = self.rc.resolve_default_room(
            room_id=self.settings.rc_room_id,
            room_name=self.settings.rc_room_name or "SNAS",
        )
        self._room_id = room["_id"]
        self._room_type = room.get("t")
        self._room_name = room.get("name") or self.settings.rc_room_name or "SNAS"
        # Admin precisa poder convidar (membro do privado ou permissão add-user-to-any-p-room)
        if self._room_type == "p" and self.rc.user_id:
            try:
                self.rc.invite_to_channel(self._room_id, self.rc.user_id, room_type="p")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Não foi possível garantir admin no SNAS: %s", exc)
        msg = (
            f"Canal padrão de entrada: #{self._room_name} "
            f"(id={self._room_id}, tipo={'privado' if self._room_type == 'p' else 'público'})"
        )
        if log:
            log(msg)
        else:
            logger.info(msg)

    def _convidar_canal_padrao(self, user_id: str) -> None:
        if not user_id:
            return
        if not self._room_id:
            self._ensure_snas_room()
        if self._room_id:
            self.rc.invite_to_channel(self._room_id, user_id, room_type=self._room_type)

    def _ensure_report_headers(self) -> None:
        if not self.erros_path.exists():
            with self.erros_path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(["linha", "email", "username", "cpf", "erro", "fase"])

        if not self.sucesso_path.exists():
            with self.sucesso_path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(
                    ["linha", "email", "username", "cpf", "user_id", "status", "fase"]
                )

    def _append_erro(
        self, linha: int, email: str, username: str, erro: str, cpf: str = "", fase: str = ""
    ) -> None:
        with self.erros_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([linha, email, username, cpf, erro[:500], fase])

    def _append_sucesso(
        self,
        linha: int,
        email: str,
        username: str,
        user_id: str,
        status: str,
        cpf: str = "",
        fase: str = "",
    ) -> None:
        with self.sucesso_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([linha, email, username, cpf, user_id, status, fase])

    def _iter_csv(self, csv_path: Path):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV sem cabeçalho")

            col_nome = self.settings.csv_col_name
            col_email = self.settings.csv_col_email
            col_cpf = self.settings.csv_col_cpf

            for idx, row in enumerate(reader, start=2):
                nome = (row.get(col_nome) or "").strip()
                email = (row.get(col_email) or "").strip().lower()
                cpf_raw = (row.get(col_cpf) or "").strip()
                yield idx, nome, email, cpf_raw

    def _sync_exports(self) -> None:
        self.checkpoint.exportar_csvs(self.settings.data_dir)

    def _registrar_existente(
        self,
        cont: FaseContadores,
        log: Callable[[str], None],
        *,
        linha: int,
        email: str,
        username_esperado: str,
        existente: dict[str, Any],
        cpf: str,
        nome: str,
    ) -> None:
        user_id = existente.get("_id", "")
        rc_username = (existente.get("username") or username_esperado).strip()
        detalhe = None
        if rc_username.lower() != username_esperado.lower():
            detalhe = f"username-rocket={rc_username} (esperado-cpf={username_esperado})"
            log(f"EXISTE  {rc_username} ({email}) — username diferente do CPF {username_esperado}")
        else:
            log(f"EXISTE  {rc_username} ({email})")
        cont.existentes += 1
        self.checkpoint.registrar(
            email,
            rc_username,
            "exists",
            user_id=user_id,
            cpf=cpf,
            nome=nome,
            detalhe=detalhe,
        )
        self._append_sucesso(linha, email, rc_username, user_id, "exists", cpf, "fase1")

# ------------------------------------------------------------------ Fase 1
    def fase1_criar(
        self,
        csv_path: Path,
        limit: int | None = None,
        on_progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        lote_id: str | None = None,
    ) -> FaseContadores:
        """Cria usuários com sendWelcomeEmail=false. Nenhum e-mail é enviado.
        Username no Rocket = CPF (somente dígitos).
        """
        if lote_id:
            lote = self.lotes.obter(lote_id)
            if not lote:
                raise ValueError(f"Lote não encontrado: {lote_id}")
            csv_path = self.lotes.path_do_lote(lote_id)
            if not csv_path.exists():
                raise FileNotFoundError(
                    f"CSV do lote {lote_id} não encontrado: {csv_path}. "
                    "Execute scripts/gerar_lotes.py"
                )
            prev = lote.status
            self.lotes.marcar_inicio(lote_id, retomar=True)
            if prev in {"em_andamento", "erro"}:
                # log after cont/log exist — set flag
                _retomada = True
            else:
                _retomada = False
        else:
            _retomada = False

        if not csv_path.exists():
            raise FileNotFoundError(f"CSV não encontrado: {csv_path}")

        cont = FaseContadores()
        log = on_progress or (lambda m: None)
        cpfs_neste_run: set[str] = set()

        if lote_id and _retomada:
            log(
                f"Retomando lote {lote_id} (status anterior interrompido) — "
                "apenas usuários ainda não criados serão provisionados"
            )

        if self.settings.dry_run:
            log("DRY-RUN: simulação da fase 1 (não cria no Rocket)")
        else:
            self.rc.login()
            log("Admin autenticado — iniciando Fase 1 (criação sem e-mail)")
            self._ensure_snas_room(log)
            log(
                "joinDefaultChannels=false — usuários entram apenas no canal "
                f"#{self._room_name or self.settings.rc_room_name}"
            )

        if lote_id:
            log(f"Processando lote {lote_id} → {csv_path.name}")

        try:
            for linha, nome, email, cpf_raw in self._iter_csv(csv_path):
                if should_cancel and should_cancel():
                    log("Cancelamento solicitado — interrompendo Fase 1")
                    break
                if limit is not None and cont.lidos >= limit:
                    break

                cont.lidos += 1
                cpf = normalizar_cpf(cpf_raw)
                username = username_from_cpf(cpf)

                try:
                    if not nome_valido(nome):
                        raise ValueError("nome obrigatório (vazio ou inválido)")
                    if not email:
                        raise ValueError("e-mail obrigatório")
                    if not validar_email(email):
                        raise ValueError(f"e-mail inválido: {email!r}")
                    if not cpf_raw:
                        raise ValueError("CPF obrigatório")
                    if cpf == CPF_ZEROS:
                        raise ValueError("CPF 00000000000 não permitido")
                    if not validar_cpf(cpf):
                        raise ValueError(f"CPF inválido: {cpf_raw!r}")
                    if not username:
                        raise ValueError("username (CPF) vazio")
                    if is_protected_username(username, self.settings.rc_admin_user):
                        raise ValueError(
                            f"username '{username}' é protegido e não pode ser provisionado por este fluxo"
                        )

                    if cpf in cpfs_neste_run or self.checkpoint.ja_cpf_usado(cpf):
                        cont.pulados += 1
                        log(f"PULADO CPF já usado como username: {cpf}")
                        continue
                    if self.checkpoint.ja_criado(email):
                        cont.pulados += 1
                        continue

                    cpfs_neste_run.add(cpf)

                    if self.settings.dry_run:
                        log(f"[DRY-RUN] criaria {username} <{email}>")
                        self.checkpoint.registrar(
                            email, username, "created", cpf=cpf, nome=nome, detalhe="dry-run-fase1"
                        )
                        cont.criados += 1
                        sleep_ms(self.settings.delay_ms)
                        continue

                    existente = self.rc.find_by_username(username)
                    if not existente:
                        existente = self.rc.find_by_email(email)

                    if existente:
                        self._registrar_existente(
                            cont,
                            log,
                            linha=linha,
                            email=email,
                            username_esperado=username,
                            existente=existente,
                            cpf=cpf,
                            nome=nome,
                        )
                    else:
                        try:
                            senha = gerar_senha()
                            user = self.rc.create_user(
                                name=nome,
                                email=email,
                                username=username,
                                password=senha,
                                require_password_change=True,
                                cpf=cpf or None,
                            )
                        except RocketChatError as exc:
                            body = (exc.body or "").lower()
                            if "already in use" in body or "error-field-unavailable" in body:
                                existente = self.rc.find_by_email(email) or self.rc.find_by_username(
                                    username
                                )
                                if existente:
                                    self._registrar_existente(
                                        cont,
                                        log,
                                        linha=linha,
                                        email=email,
                                        username_esperado=username,
                                        existente=existente,
                                        cpf=cpf,
                                        nome=nome,
                                    )
                                    sleep_ms(self.settings.delay_ms)
                                    continue
                            raise

                        user_id = user.get("_id", "")
                        cont.criados += 1
                        if user_id:
                            self._convidar_canal_padrao(user_id)
                        self.checkpoint.registrar(
                            email,
                            username,
                            "created",
                            user_id=user_id,
                            cpf=cpf,
                            nome=nome,
                            detalhe="fase1-sem-email",
                        )
                        self._append_sucesso(linha, email, username, user_id, "created", cpf, "fase1")
                        log(
                            f"CRIADO  {username} ({email}) — sem e-mail; "
                            f"canal #{self._room_name or self.settings.rc_room_name}"
                        )

                except Exception as exc:  # noqa: BLE001
                    cont.falhas += 1
                    msg = str(exc)
                    if isinstance(exc, RocketChatError):
                        msg = f"{exc} | {exc.body}"
                    log(f"FALHA linha={linha} {email}: {msg}")
                    self.checkpoint.registrar(
                        email or f"linha-{linha}",
                        username or "-",
                        "error",
                        detalhe=msg,
                        cpf=cpf,
                        nome=nome,
                    )
                    self._append_erro(linha, email, username, msg, cpf, "fase1")

                sleep_ms(self.settings.delay_ms)
                if cont.lidos % 100 == 0:
                    log(
                        f"Progresso F1: lidos={cont.lidos} criados={cont.criados} "
                        f"existentes={cont.existentes} falhas={cont.falhas}"
                    )

            self._sync_exports()
            log(
                f"Fase 1 concluída: criados={cont.criados} existentes={cont.existentes} "
                f"pulados={cont.pulados} falhas={cont.falhas}"
            )
            if lote_id:
                self.lotes.marcar_fim(
                    lote_id,
                    contadores={
                        "lidos": cont.lidos,
                        "criados": cont.criados,
                        "existentes": cont.existentes,
                        "pulados": cont.pulados,
                        "falhas": cont.falhas,
                    },
                )
                log(f"Lote {lote_id} marcado como concluído (status.json atualizado)")
            return cont
        except Exception as exc:
            if lote_id:
                try:
                    self.lotes.marcar_fim(
                        lote_id,
                        contadores={
                            "lidos": cont.lidos,
                            "criados": cont.criados,
                            "existentes": cont.existentes,
                            "pulados": cont.pulados,
                            "falhas": cont.falhas,
                        },
                        erro=str(exc),
                    )
                except Exception:  # noqa: BLE001
                    pass
            raise

    # ------------------------------------------------------------------ Fase 2
    def fase2_email_reset(
        self,
        limit: int | None = None,
        on_progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        apenas_email: str | None = None,
    ) -> FaseContadores:
        """Dispara forgotPassword (SMTP do Rocket). Não cria usuários."""
        cont = FaseContadores()
        log = on_progress or (lambda m: None)

        if self.settings.dry_run:
            log("DRY-RUN: simulação da fase 2 (não envia e-mail)")
        else:
            self.rc.login()
            log("Admin autenticado — iniciando Fase 2 (e-mail de reset)")

        if apenas_email:
            row = self.checkpoint.buscar_por_email(apenas_email)
            if not row:
                raise ValueError(f"E-mail não encontrado no checkpoint: {apenas_email}")
            if row["status"] not in {"created", "exists", "emailed"}:
                raise ValueError(f"Status inválido para fase 2: {row['status']}")
            # Individual: permite reenvio mesmo se já estiver emailed
            alvo = [row]
        else:
            alvo = self.checkpoint.elegiveis_fase2(limite=limit)

        for row in alvo:
            if should_cancel and should_cancel():
                log("Cancelamento solicitado — interrompendo Fase 2")
                break

            cont.lidos += 1
            email = row["email"]
            username = row["username"]

            try:
                if self.settings.dry_run:
                    log(f"[DRY-RUN] enviaria forgotPassword para {email}")
                    self.checkpoint.registrar(
                        email,
                        username,
                        "emailed",
                        user_id=row.get("user_id"),
                        cpf=row.get("cpf"),
                        nome=row.get("nome"),
                        detalhe="dry-run-fase2",
                    )
                    cont.emails += 1
                    continue

                self.rc.forgot_password(email)
                cont.emails += 1
                self.checkpoint.registrar(
                    email,
                    username,
                    "emailed",
                    user_id=row.get("user_id"),
                    cpf=row.get("cpf"),
                    nome=row.get("nome"),
                    detalhe="fase2-forgotPassword",
                )
                self._append_sucesso(
                    0, email, username, row.get("user_id") or "", "emailed", row.get("cpf") or "", "fase2"
                )
                log(f"E-MAIL  reset enviado → {username} ({email})")
            except Exception as exc:  # noqa: BLE001
                cont.falhas += 1
                msg = str(exc)
                if isinstance(exc, RocketChatError):
                    msg = f"{exc} | {exc.body}"
                log(f"FALHA fase2 {email}: {msg}")
                self.checkpoint.registrar(
                    email,
                    username,
                    row.get("status") or "created",
                    user_id=row.get("user_id"),
                    cpf=row.get("cpf"),
                    nome=row.get("nome"),
                    detalhe=f"fase2-erro: {msg}",
                )
                self._append_erro(0, email, username, msg, row.get("cpf") or "", "fase2")

            sleep_ms(self.settings.delay_ms)

        self._sync_exports()
        log(f"Fase 2 concluída: emails={cont.emails} falhas={cont.falhas} lidos={cont.lidos}")
        return cont

    # ----------------------------------------------------------- Delete seguro
    def excluir_usuario(
        self,
        username: str,
        *,
        confirm_username: str,
        also_recreate: bool = False,
        csv_path: Path | None = None,
    ) -> dict[str, Any]:
        """
        Exclui UM usuário pelo username exato.
        Nunca busca por e-mail. Bloqueia contas protegidas (admin, etc.).
        """
        username = (username or "").strip()
        confirm_username = (confirm_username or "").strip()

        if not username:
            raise ValueError("Informe o username a excluir.")
        if username != confirm_username:
            raise ValueError(
                "Confirmação inválida: digite o mesmo username no campo de confirmação."
            )

        assert_safe_to_delete(username, self.settings.rc_admin_user)

        result: dict[str, Any] = {
            "username": username,
            "deleted_rc": False,
            "removed_checkpoint": False,
            "recreated": False,
            "message": "",
        }

        if self.settings.dry_run:
            result["message"] = f"[DRY-RUN] excluiria '{username}'"
            self.checkpoint.remover_por_username(username)
            result["removed_checkpoint"] = True
            self._sync_exports()
            return result

        self.rc.login()

        # SOMENTE por username — nunca por e-mail (evita apagar admin por engano)
        user = self.rc.find_by_username(username)
        if not user:
            result["message"] = f"Usuário '{username}' não existe no Rocket (ok)."
            removed = self.checkpoint.remover_por_username(username)
            result["removed_checkpoint"] = removed
            self._sync_exports()
            return result

        # Dupla checagem: username retornado deve bater exatamente
        rc_username = (user.get("username") or "").strip()
        if rc_username.lower() != username.lower():
            raise PermissionError(
                f"Abortado: Rocket retornou username '{rc_username}' ≠ '{username}'."
            )

        # Se o usuário tiver role admin, bloquear
        roles = user.get("roles") or []
        if "admin" in roles:
            raise PermissionError(
                f"Exclusão bloqueada: '{username}' possui role admin no Rocket.Chat."
            )

        # Não excluir o próprio usuário autenticado
        if user.get("_id") and self.rc.user_id and user["_id"] == self.rc.user_id:
            raise PermissionError("Exclusão bloqueada: não é permitido excluir a própria sessão admin.")

        uid = user.get("_id")
        if not uid:
            raise RocketChatError("Usuário sem _id")

        self.rc.delete_user(uid)
        result["deleted_rc"] = True
        result["removed_checkpoint"] = self.checkpoint.remover_por_username(username)
        result["message"] = f"Usuário '{username}' excluído do Rocket e do checkpoint."
        logger.warning("EXCLUSÃO segura: username=%s user_id=%s", username, uid)

        self._sync_exports()

        if also_recreate and csv_path:
            # Recria só esse registro se estiver no CSV
            recriado = self._recriar_um(csv_path, username)
            result["recreated"] = recriado
            if recriado:
                result["message"] += " Recriado na Fase 1 (sem e-mail)."
            else:
                result["message"] += " Não encontrado no CSV para recriar."

        return result

    def _recriar_um(self, csv_path: Path, username: str) -> bool:
        username_l = username.strip().lower()
        encontrado: tuple[str, str, str, str] | None = None  # nome, email, username, cpf

        for _linha, nome, email, cpf_raw in self._iter_csv(csv_path):
            cpf = normalizar_cpf(cpf_raw)
            u = username_from_cpf(cpf)
            if u.lower() == username_l:
                encontrado = (nome, email, u, cpf)
                break

        if not encontrado:
            return False

        nome, email, u, cpf = encontrado
        if not nome or not email or not validar_email(email):
            raise ValueError(f"Dados inválidos no CSV para recriar '{username}'")
        if not validar_cpf(cpf):
            raise ValueError(f"CPF inválido no CSV para recriar '{username}'")

        if self.checkpoint.ja_criado(email):
            return True

        self.rc.login()
        existente = self.rc.find_by_username(u) or self.rc.find_by_email(email)
        if existente:
            self.checkpoint.registrar(
                email, u, "exists", user_id=existente.get("_id"), cpf=cpf, nome=nome
            )
            self._sync_exports()
            return True

        senha = gerar_senha()
        self._ensure_snas_room()
        user = self.rc.create_user(
            name=nome,
            email=email,
            username=u,
            password=senha,
            require_password_change=True,
            cpf=cpf or None,
        )
        if user.get("_id"):
            self._convidar_canal_padrao(user["_id"])
        self.checkpoint.registrar(
            email,
            u,
            "created",
            user_id=user.get("_id"),
            cpf=cpf,
            nome=nome,
            detalhe="recreate-fase1-sem-email",
        )
        self._sync_exports()
        return True


def start_fase_async(
    settings: Settings,
    fase: str,
    csv_path: Path,
    limit: int | None = None,
    lote_id: str | None = None,
) -> None:
    """Inicia fase 1 ou 2 em thread de fundo (para o painel)."""
    global _job

    with _job_lock:
        if _job.running:
            raise RuntimeError("Já existe um job em execução.")
        _job = JobState(running=True, fase=fase, contadores=FaseContadores())

    def _run() -> None:
        global _job
        svc = PhaseService(settings)

        def on_progress(msg: str) -> None:
            with _job_lock:
                _job.push(msg)

        def should_cancel() -> bool:
            with _job_lock:
                return _job.cancel_requested

        try:
            if fase == "fase1":
                cont = svc.fase1_criar(
                    csv_path,
                    limit=limit,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                    lote_id=lote_id,
                )
            elif fase == "fase2":
                cont = svc.fase2_email_reset(
                    limit=limit, on_progress=on_progress, should_cancel=should_cancel
                )
            else:
                raise ValueError(f"Fase desconhecida: {fase}")
            with _job_lock:
                _job.contadores = cont
        except Exception as exc:  # noqa: BLE001
            with _job_lock:
                _job.erro = str(exc)
                _job.push(f"ERRO FATAL: {exc}")
        finally:
            with _job_lock:
                _job.running = False

    threading.Thread(target=_run, daemon=True).start()


def request_cancel_job() -> None:
    with _job_lock:
        _job.cancel_requested = True
