"""
Exclusão SEGURA de um usuário — SOMENTE por username explícito.

Uso:
  python scripts/delete_user.py --username marco.memora --confirm marco.memora
  python scripts/delete_user.py --username marco.memora --confirm marco.memora --recreate
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.phases import PhaseService


def main() -> int:
    parser = argparse.ArgumentParser(description="Exclui UM usuário do Rocket por username.")
    parser.add_argument("--username", required=True, help="Username exato a excluir")
    parser.add_argument(
        "--confirm",
        required=True,
        help="Repita o mesmo username (confirmação obrigatória)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Após excluir, recria pela Fase 1 a partir do CSV",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "data" / "entrada.csv",
        help="CSV usado na recriação",
    )
    args = parser.parse_args()

    settings = load_settings()
    svc = PhaseService(settings)
    try:
        result = svc.excluir_usuario(
            args.username,
            confirm_username=args.confirm,
            also_recreate=args.recreate,
            csv_path=args.csv if args.recreate else None,
        )
    except (ValueError, PermissionError) as exc:
        print(f"BLOQUEADO: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO: {exc}")
        return 1

    print(result["message"])
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
