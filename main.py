from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.phases import PhaseService
from src.worker import ProvisionWorker


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "provision.log"

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Provisiona usuários no Rocket.Chat em duas fases "
        "(1: criar sem e-mail | 2: forgotPassword)."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "data" / "entrada.csv",
        help="Caminho do CSV de entrada (padrão: data/entrada.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Processa no máximo N linhas / elegíveis",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula sem criar usuário nem enviar e-mail real",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=None,
        help="Caminho alternativo do arquivo .env",
    )
    parser.add_argument(
        "--fase",
        choices=["1", "2", "legado"],
        default="1",
        help="1=criar sem e-mail | 2=enviar reset | legado=fluxo antigo worker",
    )
    args = parser.parse_args(argv)

    settings = load_settings(args.env)
    if args.dry_run:
        settings = replace(settings, dry_run=True)

    setup_logging(settings.logs_dir)

    if args.fase == "legado":
        logging.info("Modo legado (worker com SMTP local) | csv=%s", args.csv)
        worker = ProvisionWorker(settings)
        cont = worker.processar(args.csv, limit=args.limit)
        logging.info(
            "RESUMO legado: criados=%s emails=%s falhas=%s",
            cont.criados,
            cont.emails,
            cont.falhas,
        )
        return 1 if cont.falhas else 0

    svc = PhaseService(settings)
    if args.fase == "1":
        logging.info("Fase 1 — criar sem e-mail | csv=%s", args.csv)
        cont = svc.fase1_criar(args.csv, limit=args.limit, on_progress=logging.info)
    else:
        logging.info("Fase 2 — forgotPassword | limit=%s", args.limit)
        cont = svc.fase2_email_reset(limit=args.limit, on_progress=logging.info)

    logging.info("==== RESUMO ====")
    logging.info("Lidos:      %s", cont.lidos)
    logging.info("Pulados:    %s", cont.pulados)
    logging.info("Criados:    %s", cont.criados)
    logging.info("Existentes: %s", cont.existentes)
    logging.info("E-mails:    %s", cont.emails)
    logging.info("Falhas:     %s", cont.falhas)
    logging.info("Checkpoint: %s", settings.checkpoint_path)
    logging.info("Contagens:  %s", svc.checkpoint.contagens())

    return 1 if cont.falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
