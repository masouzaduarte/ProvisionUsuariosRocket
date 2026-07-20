from __future__ import annotations

import logging
import sys
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.phases import (
    PhaseService,
    get_job_state,
    request_cancel_job,
    start_fase_async,
)
from src.safety import protected_usernames

app = Flask(
    __name__,
    template_folder=str(ROOT / "web" / "templates"),
    static_folder=str(ROOT / "web" / "static"),
)
app.secret_key = "provision-rocket-painel-local"

settings = load_settings()
DEFAULT_CSV = ROOT / "data" / "entrada.csv"
EXEMPLO_CSV = ROOT / "data" / "entrada.exemplo.csv"


def _csv_path() -> Path:
    raw = (request.form.get("csv_path") or request.args.get("csv") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        return p
    if DEFAULT_CSV.exists():
        return DEFAULT_CSV
    return EXEMPLO_CSV


def _service() -> PhaseService:
    return PhaseService(settings)


@app.route("/")
def index():
    svc = _service()
    counts = svc.checkpoint.contagens()
    criados = svc.checkpoint.listar(status="created", limite=200)
    existentes = svc.checkpoint.listar(status="exists", limite=100)
    emailed = svc.checkpoint.listar(status="emailed", limite=200)
    erros = svc.checkpoint.listar(status="error", limite=50)
    # Inclui emailed para não “sumir” da lista após o reset
    usuarios = criados + existentes + emailed
    usuarios.sort(key=lambda x: x.get("updated_at") or "", reverse=True)

    csv_atual = DEFAULT_CSV if DEFAULT_CSV.exists() else EXEMPLO_CSV
    try:
        csv_display = str(csv_atual.relative_to(ROOT))
    except ValueError:
        csv_display = str(csv_atual)
    protegidos = sorted(protected_usernames(settings.rc_admin_user))

    return render_template(
        "index.html",
        counts=counts,
        usuarios=usuarios[:150],
        emailed=emailed,
        erros=erros,
        job=get_job_state(),
        csv_path=csv_display,
        rc_url=settings.rc_base_url,
        admin_user=settings.rc_admin_user,
        protegidos=protegidos,
        total_criados=counts.get("created", 0) + counts.get("exists", 0) + counts.get("emailed", 0),
        total_emailed=counts.get("emailed", 0),
        total_erros=counts.get("error", 0),
        elegiveis_fase2=len(svc.checkpoint.elegiveis_fase2()),
    )


@app.post("/api/fase1")
def api_fase1():
    try:
        csv_path = _csv_path()
        limit_raw = (request.form.get("limit") or "").strip()
        limit = int(limit_raw) if limit_raw else None
        start_fase_async(settings, "fase1", csv_path, limit=limit)
        flash(f"Fase 1 iniciada com CSV: {csv_path.name}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(str(exc), "danger")
    return redirect(url_for("index"))


@app.post("/api/fase2")
def api_fase2():
    try:
        limit_raw = (request.form.get("limit") or "").strip()
        limit = int(limit_raw) if limit_raw else None
        start_fase_async(settings, "fase2", _csv_path(), limit=limit)
        flash("Fase 2 iniciada (e-mails de reset via Rocket.Chat).", "success")
    except Exception as exc:  # noqa: BLE001
        flash(str(exc), "danger")
    return redirect(url_for("index"))


@app.post("/api/cancel")
def api_cancel():
    request_cancel_job()
    flash("Cancelamento solicitado.", "warning")
    return redirect(url_for("index"))


@app.get("/api/job")
def api_job():
    return jsonify(get_job_state())


@app.post("/api/excluir")
def api_excluir():
    username = (request.form.get("username") or "").strip()
    confirm = (request.form.get("confirm_username") or "").strip()
    recreate = request.form.get("recreate") == "1"

    try:
        svc = _service()
        result = svc.excluir_usuario(
            username,
            confirm_username=confirm,
            also_recreate=recreate,
            csv_path=_csv_path() if recreate else None,
        )
        flash(result["message"], "success" if result["deleted_rc"] or result["removed_checkpoint"] else "info")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "danger")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erro na exclusão: {exc}", "danger")

    return redirect(url_for("index"))


@app.post("/api/fase2/um")
def api_fase2_um():
    email = (request.form.get("email") or "").strip().lower()
    try:
        if not email:
            raise ValueError("Informe o e-mail.")
        svc = _service()
        cont = svc.fase2_email_reset(apenas_email=email)
        flash(
            f"Reset enviado para {email}. O usuário continua no Rocket "
            f"(status=emailed). emails={cont.emails} falhas={cont.falhas}",
            "success" if cont.falhas == 0 else "warning",
        )
    except Exception as exc:  # noqa: BLE001
        flash(str(exc), "danger")
    return redirect(url_for("index"))


@app.post("/api/export")
def api_export():
    try:
        svc = _service()
        svc.checkpoint.exportar_csvs(settings.data_dir)
        flash(
            "Exportados: data/usuarios_criados.csv e data/usuarios_email_enviado.csv",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        flash(str(exc), "danger")
    return redirect(url_for("index"))


def create_app() -> Flask:
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    host = "127.0.0.1"
    port = 5055
    print(f"Painel ProvisionUsuariosRocket -> http://{host}:{port}")
    print(f"Rocket: {settings.rc_base_url} | Admin: {settings.rc_admin_user}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
