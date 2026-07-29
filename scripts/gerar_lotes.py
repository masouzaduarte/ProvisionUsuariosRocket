"""Gera lotes de 10 mil a partir do EXPORT_TB_DIRIGENTE e atribui a MARCO/EVALDO/DIEGO."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.lotes import DEVS, LOTE_SIZE, LoteManager, gerar_lotes


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera lotes de provisionamento")
    parser.add_argument(
        "--fonte",
        default=str(ROOT / "data" / "EXPORT_TB_DIRIGENTE(in).csv"),
        help="CSV fonte (nome,email,cpf)",
    )
    parser.add_argument("--size", type=int, default=LOTE_SIZE, help="Tamanho do lote")
    args = parser.parse_args()

    fonte = Path(args.fonte)
    if not fonte.is_absolute():
        fonte = ROOT / fonte
    if not fonte.exists():
        raise SystemExit(f"Fonte não encontrada: {fonte}")

    settings = load_settings()
    lotes_dir = settings.data_dir / "lotes"
    print(f"Lendo {fonte} ...")
    manifest = gerar_lotes(fonte, lotes_dir, lote_size=args.size, devs=DEVS)
    mgr = LoteManager(settings.data_dir, settings.checkpoint_path)
    # Carrega status.json gerado no SQLite
    n = mgr.sync_from_status_file()
    print(f"Lotes gerados: {manifest['total_lotes']}")
    print(f"Válidos: {manifest['stats']['validos']} | Rejeitados: {manifest['stats']['rejeitados']}")
    print(f"Motivos: {manifest['stats']['motivos']}")
    print("Atribuição:")
    for dev, info in manifest["atribuicao"].items():
        print(f"  {dev}: {info['total_lotes']} lotes -> {', '.join(info['lotes'])}")
    print(f"Manifest: {lotes_dir / 'manifest.json'}")
    print(f"Status:   {lotes_dir / 'status.json'} (sync SQLite={n})")
    print(f"Guia:     {lotes_dir / 'ATRIBUICAO.md'}")


if __name__ == "__main__":
    main()
