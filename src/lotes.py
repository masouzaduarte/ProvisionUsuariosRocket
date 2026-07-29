"""Geração e controle de lotes de provisionamento (10k / 3 desenvolvedores)."""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .csv_reader import (
    CPF_ZEROS,
    nome_valido,
    normalizar_cpf,
    validar_cpf,
    validar_email,
)

DEVS = ("MARCO", "EVALDO", "DIEGO")
LOTE_SIZE = 10_000
STATUS_PENDENTE = "pendente"
STATUS_ANDAMENTO = "em_andamento"
STATUS_CONCLUIDO = "concluido"
STATUS_ERRO = "erro"


@dataclass
class LoteInfo:
    id: str
    arquivo: str
    desenvolvedor: str
    total_linhas: int
    status: str = STATUS_PENDENTE
    processados: int = 0
    criados: int = 0
    existentes: int = 0
    pulados: int = 0
    falhas: int = 0
    iniciado_em: str | None = None
    concluido_em: str | None = None
    detalhe: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _motivo_rejeicao(nome: str, email: str, cpf_raw: str) -> str | None:
    nome = (nome or "").strip()
    email = (email or "").strip().lower()
    cpf = normalizar_cpf(cpf_raw)

    if not nome_valido(nome):
        return "sem_nome"
    if not email:
        return "sem_email"
    if not validar_email(email):
        return "email_invalido"
    if not cpf:
        return "sem_cpf"
    if cpf == CPF_ZEROS:
        return "cpf_zeros"
    if not validar_cpf(cpf):
        return "cpf_invalido"
    return None


class LoteManager:
    """Lotes em data/lotes/ + status compartilhado (status.json) e espelho no checkpoint.db."""

    def __init__(self, data_dir: Path, checkpoint_path: Path):
        self.data_dir = data_dir
        self.lotes_dir = data_dir / "lotes"
        self.manifest_path = self.lotes_dir / "manifest.json"
        self.status_path = self.lotes_dir / "status.json"
        self.checkpoint_path = checkpoint_path
        self.lotes_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.sync_from_status_file()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.checkpoint_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lotes (
                    id TEXT PRIMARY KEY,
                    arquivo TEXT NOT NULL,
                    desenvolvedor TEXT NOT NULL,
                    total_linhas INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pendente',
                    processados INTEGER NOT NULL DEFAULT 0,
                    criados INTEGER NOT NULL DEFAULT 0,
                    existentes INTEGER NOT NULL DEFAULT 0,
                    pulados INTEGER NOT NULL DEFAULT 0,
                    falhas INTEGER NOT NULL DEFAULT 0,
                    iniciado_em TEXT,
                    concluido_em TEXT,
                    detalhe TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()

    def path_do_lote(self, lote_id: str) -> Path:
        return self.lotes_dir / f"{lote_id}.csv"

    def listar(
        self, desenvolvedor: str | None = None, status: str | None = None
    ) -> list[LoteInfo]:
        sql = "SELECT * FROM lotes WHERE 1=1"
        params: list[Any] = []
        if desenvolvedor:
            sql += " AND desenvolvedor = ?"
            params.append(desenvolvedor.strip().upper())
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_lote(r) for r in rows]

    def obter(self, lote_id: str) -> LoteInfo | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
        return self._row_to_lote(row) if row else None

    @staticmethod
    def _row_to_lote(row: sqlite3.Row) -> LoteInfo:
        return LoteInfo(
            id=row["id"],
            arquivo=row["arquivo"],
            desenvolvedor=row["desenvolvedor"],
            total_linhas=int(row["total_linhas"] or 0),
            status=row["status"],
            processados=int(row["processados"] or 0),
            criados=int(row["criados"] or 0),
            existentes=int(row["existentes"] or 0),
            pulados=int(row["pulados"] or 0),
            falhas=int(row["falhas"] or 0),
            iniciado_em=row["iniciado_em"],
            concluido_em=row["concluido_em"],
            detalhe=row["detalhe"],
        )

    def upsert(self, lote: LoteInfo, *, sync_file: bool = True) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO lotes (
                    id, arquivo, desenvolvedor, total_linhas, status,
                    processados, criados, existentes, pulados, falhas,
                    iniciado_em, concluido_em, detalhe, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    arquivo = excluded.arquivo,
                    desenvolvedor = excluded.desenvolvedor,
                    total_linhas = excluded.total_linhas,
                    status = excluded.status,
                    processados = excluded.processados,
                    criados = excluded.criados,
                    existentes = excluded.existentes,
                    pulados = excluded.pulados,
                    falhas = excluded.falhas,
                    iniciado_em = excluded.iniciado_em,
                    concluido_em = excluded.concluido_em,
                    detalhe = excluded.detalhe,
                    updated_at = datetime('now')
                """,
                (
                    lote.id,
                    lote.arquivo,
                    lote.desenvolvedor,
                    lote.total_linhas,
                    lote.status,
                    lote.processados,
                    lote.criados,
                    lote.existentes,
                    lote.pulados,
                    lote.falhas,
                    lote.iniciado_em,
                    lote.concluido_em,
                    lote.detalhe,
                ),
            )
            conn.commit()
        if sync_file:
            self.export_status_file()

    def marcar_inicio(self, lote_id: str) -> LoteInfo:
        lote = self.obter(lote_id)
        if not lote:
            raise ValueError(f"Lote não encontrado: {lote_id}")
        if lote.status == STATUS_CONCLUIDO:
            raise ValueError(f"Lote {lote_id} já está concluído.")
        lote.status = STATUS_ANDAMENTO
        lote.iniciado_em = lote.iniciado_em or _now()
        lote.detalhe = "fase1 em andamento"
        self.upsert(lote)
        return lote

    def marcar_fim(
        self,
        lote_id: str,
        *,
        contadores: dict[str, int] | None = None,
        erro: str | None = None,
    ) -> LoteInfo:
        lote = self.obter(lote_id)
        if not lote:
            raise ValueError(f"Lote não encontrado: {lote_id}")
        c = contadores or {}
        lote.processados = int(c.get("lidos", lote.processados))
        lote.criados = int(c.get("criados", lote.criados))
        lote.existentes = int(c.get("existentes", lote.existentes))
        lote.pulados = int(c.get("pulados", lote.pulados))
        lote.falhas = int(c.get("falhas", lote.falhas))
        if erro:
            lote.status = STATUS_ERRO
            lote.detalhe = erro[:500]
        else:
            lote.status = STATUS_CONCLUIDO
            lote.concluido_em = _now()
            lote.detalhe = (
                f"criados={lote.criados} existentes={lote.existentes} "
                f"pulados={lote.pulados} falhas={lote.falhas}"
            )
        self.upsert(lote)
        return lote

    def marcar_status_manual(self, lote_id: str, status: str, detalhe: str | None = None) -> LoteInfo:
        if status not in {STATUS_PENDENTE, STATUS_ANDAMENTO, STATUS_CONCLUIDO, STATUS_ERRO}:
            raise ValueError(f"Status inválido: {status}")
        lote = self.obter(lote_id)
        if not lote:
            raise ValueError(f"Lote não encontrado: {lote_id}")
        lote.status = status
        if detalhe is not None:
            lote.detalhe = detalhe
        if status == STATUS_CONCLUIDO and not lote.concluido_em:
            lote.concluido_em = _now()
        if status == STATUS_PENDENTE:
            lote.concluido_em = None
            lote.iniciado_em = None
        self.upsert(lote)
        return lote

    def export_status_file(self) -> None:
        lotes = [l.to_dict() for l in self.listar()]
        payload = {
            "atualizado_em": _now(),
            "devs": list(DEVS),
            "lote_size": LOTE_SIZE,
            "lotes": lotes,
        }
        self.status_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def sync_from_status_file(self) -> int:
        """Importa status.json (compartilhado via git) para o SQLite local."""
        if not self.status_path.exists():
            return 0
        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        n = 0
        for item in data.get("lotes") or []:
            lote = LoteInfo(
                id=item["id"],
                arquivo=item.get("arquivo") or f"{item['id']}.csv",
                desenvolvedor=(item.get("desenvolvedor") or "").upper(),
                total_linhas=int(item.get("total_linhas") or 0),
                status=item.get("status") or STATUS_PENDENTE,
                processados=int(item.get("processados") or 0),
                criados=int(item.get("criados") or 0),
                existentes=int(item.get("existentes") or 0),
                pulados=int(item.get("pulados") or 0),
                falhas=int(item.get("falhas") or 0),
                iniciado_em=item.get("iniciado_em"),
                concluido_em=item.get("concluido_em"),
                detalhe=item.get("detalhe"),
            )
            self.upsert(lote, sync_file=False)
            n += 1
        return n

    def resumo_por_dev(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {
            d: {"total": 0, "pendente": 0, "em_andamento": 0, "concluido": 0, "erro": 0, "linhas": 0}
            for d in DEVS
        }
        for lote in self.listar():
            d = lote.desenvolvedor.upper()
            if d not in out:
                out[d] = {
                    "total": 0,
                    "pendente": 0,
                    "em_andamento": 0,
                    "concluido": 0,
                    "erro": 0,
                    "linhas": 0,
                }
            out[d]["total"] += 1
            out[d]["linhas"] += lote.total_linhas
            key = lote.status if lote.status in out[d] else "pendente"
            if key in out[d]:
                out[d][key] += 1
        return out


def _iter_linhas_validas(csv_path: Path) -> Iterator[tuple[str, str, str]]:
    """Yield (nome, email, cpf) únicos por CPF, já filtrados."""
    seen_cpf: set[str] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nome = (row.get("nome") or "").strip()
            email = (row.get("email") or "").strip().lower()
            cpf_raw = (row.get("cpf") or "").strip()
            motivo = _motivo_rejeicao(nome, email, cpf_raw)
            if motivo:
                continue
            cpf = normalizar_cpf(cpf_raw)
            if cpf in seen_cpf:
                continue
            seen_cpf.add(cpf)
            yield nome, email, cpf


def gerar_lotes(
    fonte: Path,
    lotes_dir: Path,
    *,
    lote_size: int = LOTE_SIZE,
    devs: tuple[str, ...] = DEVS,
) -> dict[str, Any]:
    """
    Lê o EXPORT, filtra inválidos/duplicados de CPF e gera CSVs de 10k
    atribuídos em rodízio a MARCO, EVALDO e DIEGO.
    """
    lotes_dir.mkdir(parents=True, exist_ok=True)
    for old in lotes_dir.glob("lote_*.csv"):
        old.unlink()

    rejeitados_path = lotes_dir / "rejeitados.csv"
    stats = {
        "lidos": 0,
        "validos": 0,
        "rejeitados": 0,
        "cpf_duplicado": 0,
        "motivos": {},
    }

    seen_cpf: set[str] = set()
    validos: list[tuple[str, str, str]] = []

    with fonte.open("r", encoding="utf-8-sig", newline="") as fin, rejeitados_path.open(
        "w", encoding="utf-8", newline=""
    ) as frej:
        reader = csv.DictReader(fin)
        wrej = csv.writer(frej)
        wrej.writerow(["nome", "email", "cpf", "motivo"])

        for row in reader:
            stats["lidos"] += 1
            nome = (row.get("nome") or "").strip()
            email = (row.get("email") or "").strip().lower()
            cpf_raw = (row.get("cpf") or "").strip()
            motivo = _motivo_rejeicao(nome, email, cpf_raw)
            if motivo:
                stats["rejeitados"] += 1
                stats["motivos"][motivo] = stats["motivos"].get(motivo, 0) + 1
                wrej.writerow([nome, email, cpf_raw, motivo])
                continue
            cpf = normalizar_cpf(cpf_raw)
            if cpf in seen_cpf:
                stats["cpf_duplicado"] += 1
                stats["motivos"]["cpf_duplicado"] = stats["motivos"].get("cpf_duplicado", 0) + 1
                wrej.writerow([nome, email, cpf, "cpf_duplicado"])
                continue
            seen_cpf.add(cpf)
            validos.append((nome, email, cpf))
            stats["validos"] += 1

    lotes_meta: list[dict[str, Any]] = []
    total_lotes = (len(validos) + lote_size - 1) // lote_size if validos else 0

    for i in range(total_lotes):
        chunk = validos[i * lote_size : (i + 1) * lote_size]
        lote_num = i + 1
        lote_id = f"lote_{lote_num:03d}"
        dev = devs[i % len(devs)]
        arquivo = f"{lote_id}.csv"
        path = lotes_dir / arquivo
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["nome", "email", "cpf"])
            for nome, email, cpf in chunk:
                w.writerow([nome, email, cpf])
        lotes_meta.append(
            {
                "id": lote_id,
                "arquivo": arquivo,
                "desenvolvedor": dev,
                "total_linhas": len(chunk),
                "status": STATUS_PENDENTE,
            }
        )

    # README de atribuição
    por_dev = {d: [] for d in devs}
    for m in lotes_meta:
        por_dev[m["desenvolvedor"]].append(m["id"])

    manifest = {
        "fonte": str(fonte.name),
        "gerado_em": _now(),
        "lote_size": lote_size,
        "devs": list(devs),
        "stats": stats,
        "total_lotes": total_lotes,
        "atribuicao": {d: {"lotes": ids, "total_lotes": len(ids)} for d, ids in por_dev.items()},
        "lotes": lotes_meta,
    }
    (lotes_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # status.json inicial (preserva concluídos se já existirem)
    status_path = lotes_dir / "status.json"
    prev_status: dict[str, dict[str, Any]] = {}
    if status_path.exists():
        try:
            prev = json.loads(status_path.read_text(encoding="utf-8"))
            for item in prev.get("lotes") or []:
                prev_status[item["id"]] = item
        except (OSError, json.JSONDecodeError):
            pass

    status_lotes = []
    for m in lotes_meta:
        old = prev_status.get(m["id"]) or {}
        merged = {
            **m,
            "processados": old.get("processados", 0),
            "criados": old.get("criados", 0),
            "existentes": old.get("existentes", 0),
            "pulados": old.get("pulados", 0),
            "falhas": old.get("falhas", 0),
            "status": old.get("status", STATUS_PENDENTE)
            if old.get("status") == STATUS_CONCLUIDO
            else STATUS_PENDENTE,
            "iniciado_em": old.get("iniciado_em") if old.get("status") == STATUS_CONCLUIDO else None,
            "concluido_em": old.get("concluido_em")
            if old.get("status") == STATUS_CONCLUIDO
            else None,
            "detalhe": old.get("detalhe") if old.get("status") == STATUS_CONCLUIDO else None,
        }
        # Se regenerou com mesmo id e estava concluído, mantém
        if old.get("status") == STATUS_CONCLUIDO:
            merged["status"] = STATUS_CONCLUIDO
        status_lotes.append(merged)

    status_path.write_text(
        json.dumps(
            {
                "atualizado_em": _now(),
                "devs": list(devs),
                "lote_size": lote_size,
                "lotes": status_lotes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    atrib_txt = lotes_dir / "ATRIBUICAO.md"
    lines = [
        "# Atribuição de lotes",
        "",
        f"Fonte: `{fonte.name}`",
        f"Tamanho do lote: **{lote_size}** usuários válidos",
        f"Total válidos: **{stats['validos']}** | Lotes: **{total_lotes}**",
        f"Rejeitados: **{stats['rejeitados']}** | CPF duplicado: **{stats['cpf_duplicado']}**",
        "",
    ]
    for d in devs:
        ids = por_dev[d]
        linhas = sum(m["total_linhas"] for m in lotes_meta if m["desenvolvedor"] == d)
        lines.append(f"## {d}")
        lines.append(f"- Lotes: {len(ids)} ({', '.join(ids)})")
        lines.append(f"- Usuários: {linhas}")
        lines.append("")
    atrib_txt.write_text("\n".join(lines), encoding="utf-8")

    return manifest
