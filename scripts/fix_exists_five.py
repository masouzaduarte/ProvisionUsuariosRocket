"""Registra no checkpoint as 5 contas que já existiam (username antigo)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.phases import PhaseService

ROWS = [
    ("Francilda Rodrigues de Matos", "francilda.matos@mds.gov.br", "65910125168"),
    ("Fabio Dias de Andrade", "fabio.andrade@mds.gov.br", "97532878104"),
    ("Tatiana Carvalho França", "tatiana.franca@mds.gov.br", "75819074300"),
    ("Diego correa Couto", "diego.couto@mds.gov.br", "12080877704"),
    ("Andrezza Pavetits", "andreza.pavetits@mds.gov.br", "80218466153"),
]


def main() -> int:
    settings = load_settings()
    svc = PhaseService(settings)
    svc.rc.login()

    for nome, email, cpf in ROWS:
        user = svc.rc.find_by_email(email)
        if not user:
            print("NAO ENCONTRADO", email)
            continue
        # limpa status error anterior
        svc.checkpoint.registrar(
            email,
            (user.get("username") or "").strip(),
            "exists",
            user_id=user.get("_id"),
            cpf=cpf,
            nome=nome,
            detalhe=f"username-rocket={user.get('username')} (esperado-cpf={cpf})",
        )
        roles = user.get("roles") or []
        flag = " [ADMIN]" if "admin" in roles else ""
        print(
            f"OK {email} -> username={user.get('username')} id={user.get('_id')}{flag}"
        )

    svc.checkpoint.exportar_csvs(settings.data_dir)
    print("contagens", svc.checkpoint.contagens())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
