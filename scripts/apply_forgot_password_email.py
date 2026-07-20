"""
Aplica o template de e-mail Forgot Password no Rocket.Chat.

Uso:
  python scripts/apply_forgot_password_email.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.rocketchat import RocketChatClient, RocketChatError

SUBJECT = "Defina sua senha — Rocket.Chat Ministério do Desenvolvimento Social"

TEMPLATE_PATH = ROOT / "templates" / "forgot-password.html"


def main() -> int:
    settings = load_settings()
    body = TEMPLATE_PATH.read_text(encoding="utf-8").strip()

    rc = RocketChatClient(settings.rc_base_url, settings.rc_admin_user, settings.rc_admin_password)
    rc.login()

    try:
        # Algumas versões usam flag de customização
        try:
            rc.update_setting("Forgot_Password_Customized", True)
            print("Forgot_Password_Customized = true")
        except RocketChatError as exc:
            print(f"(opcional) Forgot_Password_Customized: {exc}")

        rc.update_setting("Forgot_Password_Email_Subject", SUBJECT)
        print("Subject atualizado")

        rc.update_setting("Forgot_Password_Email", body)
        print("Body atualizado")

        print("---")
        print("Subject atual:", rc.get_setting("Forgot_Password_Email_Subject"))
        print("Body (primeiros 200 chars):", str(rc.get_setting("Forgot_Password_Email"))[:200])
    except RocketChatError as exc:
        print(f"ERRO: {exc}")
        if exc.body:
            print(exc.body[:500])
        return 1

    print("OK — template Forgot Password aplicado no Rocket.Chat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
