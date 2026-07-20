from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from .checkpoint import Checkpoint
from .config import Settings
from .csv_reader import UsuarioEntrada, normalizar_cpf, username_from_cpf, validar_cpf, validar_email
from .mailer import Mailer
from .password import gerar_senha
from .rocketchat import RocketChatClient, RocketChatError, sleep_ms

logger = logging.getLogger(__name__)


@dataclass
class Contadores:
    lidos: int = 0
    pulados: int = 0
    criados: int = 0
    existentes: int = 0
    emails: int = 0
    falhas: int = 0


class ProvisionWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.checkpoint = Checkpoint(settings.checkpoint_path)
        self.rc = RocketChatClient(
            settings.rc_base_url,
            settings.rc_admin_user,
            settings.rc_admin_password,
        )
        self.mailer = Mailer(
            host=settings.smtp_host or "localhost",
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_addr=settings.smtp_from,
            from_name=settings.smtp_from_name,
            template_path=settings.template_path,
            app_url=settings.rc_base_url,
            dry_run=settings.dry_run,
        )
        self.erros_path = settings.data_dir / "erros.csv"
        self.sucesso_path = settings.data_dir / "sucesso.csv"
        self._ensure_report_headers()

    def _ensure_report_headers(self) -> None:
        if not self.erros_path.exists():
            with self.erros_path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(["linha", "email", "username", "cpf", "erro"])

        if not self.sucesso_path.exists():
            cols = ["linha", "email", "username", "cpf", "user_id", "status"]
            if self.settings.store_password_in_success:
                cols.append("senha")
            with self.sucesso_path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(cols)

    def _append_erro(self, linha: int, email: str, username: str, erro: str, cpf: str = "") -> None:
        with self.erros_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([linha, email, username, cpf, erro[:500]])

    def _append_sucesso(
        self,
        linha: int,
        email: str,
        username: str,
        user_id: str,
        status: str,
        senha: str | None = None,
        cpf: str = "",
    ) -> None:
        row = [linha, email, username, cpf, user_id, status]
        if self.settings.store_password_in_success:
            row.append(senha or "")
        with self.sucesso_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(row)

    def _iter_csv(self, csv_path: Path):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV sem cabeçalho")

            col_nome = self.settings.csv_col_name
            col_email = self.settings.csv_col_email
            col_cpf = self.settings.csv_col_cpf

            for idx, row in enumerate(reader, start=2):  # linha 1 = header
                nome = (row.get(col_nome) or "").strip()
                email = (row.get(col_email) or "").strip().lower()
                cpf_raw = (row.get(col_cpf) or "").strip()

                yield idx, nome, email, cpf_raw

    def processar(self, csv_path: Path, limit: int | None = None) -> Contadores:
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV não encontrado: {csv_path}")

        cont = Contadores()

        if not self.settings.dry_run:
            if not self.settings.skip_email and not self.settings.smtp_host:
                raise ValueError("SMTP_HOST é obrigatório no .env (ou use --dry-run / SKIP_EMAIL=true)")
            self.rc.login()
        else:
            logger.info("DRY-RUN ativo: não cria usuários reais nem envia e-mail SMTP")

        for linha, nome, email, cpf_raw in self._iter_csv(csv_path):
            if limit is not None and cont.lidos >= limit:
                break

            cont.lidos += 1
            cpf = normalizar_cpf(cpf_raw)
            username = username_from_cpf(cpf)

            try:
                if not nome:
                    raise ValueError("nome vazio")
                if not email or not validar_email(email):
                    raise ValueError(f"e-mail inválido: {email!r}")
                if not cpf_raw or not validar_cpf(cpf):
                    raise ValueError(f"CPF obrigatório/inválido: {cpf_raw!r}")
                if not username:
                    raise ValueError("username (CPF) vazio")

                if self.checkpoint.ja_processado_ok(email):
                    cont.pulados += 1
                    if cont.lidos % 1000 == 0:
                        logger.info("Progresso: lidos=%s pulados=%s criados=%s", cont.lidos, cont.pulados, cont.criados)
                    continue

                if self.settings.dry_run:
                    senha = gerar_senha()
                    logger.info(
                        "[DRY-RUN] linha=%s criar user=%s email=%s cpf=%s senha=%s",
                        linha,
                        username,
                        email,
                        cpf or "-",
                        senha,
                    )
                    self.mailer.send_password_email(
                        to_email=email, nome=nome, username=username, senha=senha
                    )
                    self.checkpoint.registrar(email, username, "created", detalhe="dry-run")
                    cont.criados += 1
                    cont.emails += 1
                    sleep_ms(self.settings.delay_ms)
                    continue

                existente = self.rc.find_by_username(username)
                if not existente:
                    existente = self.rc.find_by_email(email)

                senha: str | None = None
                status: str

                if existente:
                    user_id = existente.get("_id", "")
                    status = "exists"
                    cont.existentes += 1
                    logger.info("EXISTE  %s (%s)", username, email)
                    # Usuário já existe: não reenvia senha automaticamente
                    self.checkpoint.registrar(email, username, status, user_id=user_id)
                    self._append_sucesso(linha, email, username, user_id, status, cpf=cpf)
                else:
                    senha = gerar_senha()
                    user = self.rc.create_user(
                        name=nome,
                        email=email,
                        username=username,
                        password=senha,
                        require_password_change=True,
                        cpf=cpf or None,
                    )
                    user_id = user.get("_id", "")
                    status = "created"
                    cont.criados += 1
                    logger.info("CRIADO  %s (%s) cpf=%s", username, email, cpf or "-")

                    if self.settings.rc_room_id and user_id:
                        self.rc.invite_to_channel(self.settings.rc_room_id, user_id)

                    if self.settings.skip_email:
                        logger.warning(
                            "SKIP_EMAIL: senha NÃO enviada por e-mail | user=%s | senha=%s",
                            username,
                            senha,
                        )
                        self.checkpoint.registrar(email, username, "created", user_id=user_id, detalhe="skip-email")
                        self._append_sucesso(linha, email, username, user_id, "created", senha, cpf=cpf)
                    else:
                        self.mailer.send_password_email(
                            to_email=email, nome=nome, username=username, senha=senha
                        )
                        cont.emails += 1
                        self.checkpoint.registrar(email, username, "emailed", user_id=user_id)
                        self._append_sucesso(linha, email, username, user_id, "emailed", senha, cpf=cpf)

            except Exception as exc:  # noqa: BLE001 - queremos capturar tudo no lote
                cont.falhas += 1
                msg = str(exc)
                if isinstance(exc, RocketChatError):
                    msg = f"{exc} | {exc.body}"
                logger.error("FALHA linha=%s email=%s :: %s", linha, email, msg)
                self.checkpoint.registrar(email or f"linha-{linha}", username or "-", "error", detalhe=msg)
                self._append_erro(linha, email, username, msg, cpf=cpf)

            sleep_ms(self.settings.delay_ms)

            if cont.lidos % 500 == 0:
                logger.info(
                    "Progresso: lidos=%s criados=%s existentes=%s emails=%s falhas=%s pulados=%s",
                    cont.lidos,
                    cont.criados,
                    cont.existentes,
                    cont.emails,
                    cont.falhas,
                    cont.pulados,
                )

        return cont
