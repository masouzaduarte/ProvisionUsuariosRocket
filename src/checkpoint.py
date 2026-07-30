from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Checkpoint:
    """Persiste progresso das fases 1 (criação) e 2 (e-mail de reset)."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processados (
                    email TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_id TEXT,
                    cpf TEXT,
                    nome TEXT,
                    detalhe TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(processados)").fetchall()}
            if "cpf" not in cols:
                conn.execute("ALTER TABLE processados ADD COLUMN cpf TEXT")
            if "nome" not in cols:
                conn.execute("ALTER TABLE processados ADD COLUMN nome TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processados_username ON processados(username)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processados_status ON processados(status)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    chave TEXT PRIMARY KEY,
                    valor TEXT
                )
                """
            )
            conn.commit()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ja_criado(self, email: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM processados WHERE email = ? COLLATE NOCASE",
                (email.strip().lower(),),
            ).fetchone()
            return bool(row and row["status"] in {"created", "exists", "emailed"})

    def ja_cpf_usado(self, cpf: str) -> bool:
        """True se o CPF já foi usado como username em criação bem-sucedida."""
        digits = (cpf or "").strip()
        if not digits:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status FROM processados
                WHERE (username = ? OR cpf = ?)
                  AND status IN ('created', 'exists', 'emailed')
                LIMIT 1
                """,
                (digits, digits),
            ).fetchone()
        return bool(row)

    def ja_email_enviado(self, email: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM processados WHERE email = ? COLLATE NOCASE",
                (email.strip().lower(),),
            ).fetchone()
            return bool(row and row["status"] == "emailed")

    def ja_processado_ok(self, email: str) -> bool:
        return self.ja_criado(email)

    def registrar(
        self,
        email: str,
        username: str,
        status: str,
        user_id: str | None = None,
        detalhe: str | None = None,
        cpf: str | None = None,
        nome: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processados (email, username, status, user_id, cpf, nome, detalhe, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(email) DO UPDATE SET
                    username = excluded.username,
                    status = excluded.status,
                    user_id = COALESCE(excluded.user_id, processados.user_id),
                    cpf = COALESCE(excluded.cpf, processados.cpf),
                    nome = COALESCE(excluded.nome, processados.nome),
                    detalhe = excluded.detalhe,
                    updated_at = datetime('now')
                """,
                (
                    email.strip().lower(),
                    username,
                    status,
                    user_id,
                    cpf,
                    nome,
                    detalhe,
                ),
            )
            conn.commit()

    def contagens(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM processados GROUP BY status"
            ).fetchall()
        return {row["status"]: row["total"] for row in rows}

    def listar(self, status: str | None = None, limite: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT email, username, status, user_id, cpf, nome, detalhe, updated_at
                    FROM processados
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (status, limite),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT email, username, status, user_id, cpf, nome, detalhe, updated_at
                    FROM processados
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limite,),
                ).fetchall()
        return [dict(r) for r in rows]

    def buscar_por_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT email, username, status, user_id, cpf, nome, detalhe, updated_at
                FROM processados
                WHERE username = ? COLLATE NOCASE
                LIMIT 1
                """,
                (username.strip(),),
            ).fetchone()
        return dict(row) if row else None

    def buscar_por_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT email, username, status, user_id, cpf, nome, detalhe, updated_at
                FROM processados
                WHERE email = ? COLLATE NOCASE
                LIMIT 1
                """,
                (email.strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def remover_por_username(self, username: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM processados WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            )
            conn.commit()
            return cur.rowcount > 0

    def elegiveis_fase2(self, limite: int | None = None) -> list[dict[str, Any]]:
        """Usuários criados/existentes que ainda não receberam e-mail de reset."""
        sql = """
            SELECT email, username, status, user_id, cpf, nome, detalhe, updated_at
            FROM processados
            WHERE status IN ('created', 'exists')
            ORDER BY updated_at ASC
        """
        with self._connect() as conn:
            if limite:
                rows = conn.execute(sql + " LIMIT ?", (limite,)).fetchall()
            else:
                rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def exportar_csvs(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        criados = self.listar(limite=1_000_000)
        criados = [u for u in criados if u["status"] in {"created", "exists", "emailed"}]
        emailed = [u for u in criados if u["status"] == "emailed"]

        criados_path = data_dir / "usuarios_criados.csv"
        emailed_path = data_dir / "usuarios_email_enviado.csv"

        cols = ["nome", "email", "username", "cpf", "user_id", "status", "updated_at"]
        with criados_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for u in criados:
                w.writerow(u)

        with emailed_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for u in emailed:
                w.writerow(u)
