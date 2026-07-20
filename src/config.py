from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


@dataclass(frozen=True)
class Settings:
    rc_base_url: str
    rc_admin_user: str
    rc_admin_password: str
    rc_room_id: str

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_tls: bool
    smtp_from: str
    smtp_from_name: str

    batch_size: int
    delay_ms: int
    max_retries: int
    dry_run: bool
    store_password_in_success: bool
    skip_email: bool

    csv_col_name: str
    csv_col_email: str
    csv_col_username: str
    csv_col_cpf: str

    data_dir: Path
    checkpoint_path: Path
    template_path: Path
    logs_dir: Path


def load_settings(env_file: Path | None = None) -> Settings:
    env_path = env_file or (ROOT / ".env")
    # Remove BOM se o arquivo foi salvo como UTF-8 com BOM (comum no Windows)
    if env_path.exists():
        raw = env_path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            env_path.write_bytes(raw[3:])
    load_dotenv(env_path, override=True)

    data_dir = ROOT / "data"
    logs_dir = ROOT / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    base_url = os.getenv("RC_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("RC_BASE_URL é obrigatório no .env")

    admin_user = os.getenv("RC_ADMIN_USER", "").strip()
    admin_password = os.getenv("RC_ADMIN_PASSWORD", "").strip()
    if not admin_user or not admin_password:
        raise ValueError("RC_ADMIN_USER e RC_ADMIN_PASSWORD são obrigatórios no .env")

    smtp_host = os.getenv("SMTP_HOST", "").strip()

    return Settings(
        rc_base_url=base_url,
        rc_admin_user=admin_user,
        rc_admin_password=admin_password,
        rc_room_id=os.getenv("RC_ROOM_ID", "").strip(),
        smtp_host=smtp_host,
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
        smtp_use_tls=_bool(os.getenv("SMTP_USE_TLS"), True),
        smtp_from=os.getenv("SMTP_FROM", "noreply@localhost").strip(),
        smtp_from_name=os.getenv("SMTP_FROM_NAME", "Rocket.Chat").strip(),
        batch_size=max(1, int(os.getenv("BATCH_SIZE", "100"))),
        delay_ms=max(0, int(os.getenv("DELAY_MS", "100"))),
        max_retries=max(1, int(os.getenv("MAX_RETRIES", "3"))),
        dry_run=_bool(os.getenv("DRY_RUN"), False),
        store_password_in_success=_bool(os.getenv("STORE_PASSWORD_IN_SUCCESS"), False),
        skip_email=_bool(os.getenv("SKIP_EMAIL"), False),
        csv_col_name=os.getenv("CSV_COL_NAME", "nome").strip(),
        csv_col_email=os.getenv("CSV_COL_EMAIL", "email").strip(),
        csv_col_username=os.getenv("CSV_COL_USERNAME", "username").strip(),
        csv_col_cpf=os.getenv("CSV_COL_CPF", "cpf").strip(),
        data_dir=data_dir,
        checkpoint_path=data_dir / "checkpoint.db",
        template_path=ROOT / "templates" / "email-senha.html",
        logs_dir=logs_dir,
    )
