from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


class Mailer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        use_tls: bool,
        from_addr: str,
        from_name: str,
        template_path: Path,
        app_url: str,
        dry_run: bool = False,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.from_addr = from_addr
        self.from_name = from_name
        self.template = template_path.read_text(encoding="utf-8")
        self.app_url = app_url
        self.dry_run = dry_run

    def _render(self, nome: str, username: str, senha: str) -> str:
        return (
            self.template.replace("{{nome}}", nome)
            .replace("{{username}}", username)
            .replace("{{senha}}", senha)
            .replace("{{url}}", self.app_url)
        )

    def send_password_email(self, *, to_email: str, nome: str, username: str, senha: str) -> None:
        html = self._render(nome, username, senha)
        subject = "Sua conta no Rocket.Chat — senha temporária"

        if self.dry_run:
            logger.info("[DRY-RUN] E-mail para %s | user=%s | senha=%s", to_email, username, senha)
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.from_name} <{self.from_addr}>"
        msg["To"] = to_email

        texto = (
            f"Olá, {nome}\n\n"
            f"Usuário: {username}\n"
            f"Senha temporária: {senha}\n\n"
            f"No primeiro acesso você deverá trocar a senha.\n"
            f"Acesse: {self.app_url}\n"
        )
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        if self.use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(self.host, self.port, timeout=60) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if self.user:
                    server.login(self.user, self.password)
                server.sendmail(self.from_addr, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(self.host, self.port, timeout=60) as server:
                if self.user:
                    server.login(self.user, self.password)
                server.sendmail(self.from_addr, [to_email], msg.as_string())

        logger.info("E-mail enviado para %s", to_email)
