"""Processa um lote (ou o próximo pendente do dev) sem painel — útil em paralelo."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.lotes import DEVS, STATUS_PENDENTE
from src.phases import PhaseService


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa lote(s) em paralelo via CLI")
    parser.add_argument("--lote", help="ID do lote (ex.: lote_001)")
    parser.add_argument("--dev", choices=DEVS, help="Pega o próximo lote pendente deste dev")
    parser.add_argument("--limit", type=int, default=None, help="Limite de usuários (teste)")
    parser.add_argument("--todos-pendentes", action="store_true", help="Processa todos os pendentes do --dev")
    args = parser.parse_args()

    if not args.lote and not args.dev:
        raise SystemExit("Informe --lote ou --dev")

    settings = load_settings()
    svc = PhaseService(settings)

    def _run_one(lote_id: str) -> None:
        print(f"=== Iniciando {lote_id} ===")
        cont = svc.fase1_criar(
            Path("."),
            limit=args.limit,
            lote_id=lote_id,
            on_progress=print,
        )
        print(
            f"=== Fim {lote_id}: criados={cont.criados} existentes={cont.existentes} "
            f"pulados={cont.pulados} falhas={cont.falhas} ==="
        )

    if args.lote:
        _run_one(args.lote)
        return

    pendentes = [
        l for l in svc.lotes.listar(desenvolvedor=args.dev) if l.status == STATUS_PENDENTE
    ]
    if not pendentes:
        print(f"Nenhum lote pendente para {args.dev}")
        return

    if args.todos_pendentes:
        for lote in pendentes:
            _run_one(lote.id)
    else:
        _run_one(pendentes[0].id)


if __name__ == "__main__":
    main()
