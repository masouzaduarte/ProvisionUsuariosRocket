from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class RocketChatError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or ""


class RocketChatClient:
    def __init__(self, base_url: str, admin_user: str, admin_password: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.timeout = timeout
        self.session = requests.Session()
        self.auth_token: str | None = None
        self.user_id: str | None = None

    def login(self) -> None:
        url = f"{self.base_url}/api/v1/login"
        resp = self.session.post(
            url,
            json={"user": self.admin_user, "password": self.admin_password},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RocketChatError("Falha no login admin", resp.status_code, resp.text)

        data = resp.json()
        if not data.get("status") == "success" and "data" not in data:
            # Algumas versões retornam status success; outras só data
            if "data" not in data:
                raise RocketChatError("Resposta de login inválida", resp.status_code, resp.text)

        payload = data.get("data") or {}
        self.auth_token = payload.get("authToken")
        self.user_id = payload.get("userId")
        if not self.auth_token or not self.user_id:
            raise RocketChatError("Token/userId ausentes no login", resp.status_code, resp.text)

        self.session.headers.update(
            {
                "X-Auth-Token": self.auth_token,
                "X-User-Id": self.user_id,
                "Content-Type": "application/json",
            }
        )
        logger.info("Admin autenticado no Rocket.Chat")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.auth_token:
            self.login()

        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)

        if resp.status_code in {401, 403}:
            # tenta relogar uma vez
            self.login()
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)

        if resp.status_code >= 400:
            raise RocketChatError(f"Erro {method} {path}", resp.status_code, resp.text)

        try:
            return resp.json()
        except ValueError as exc:
            raise RocketChatError("JSON inválido", resp.status_code, resp.text) from exc

    def find_by_username(self, username: str) -> dict[str, Any] | None:
        try:
            data = self._request("GET", f"users.info?username={username}")
            return data.get("user")
        except RocketChatError as exc:
            if exc.status_code == 400 or "error-user-not-found" in (exc.body or ""):
                return None
            # Rocket às vezes retorna 400 com user not found
            if "not found" in (exc.body or "").lower():
                return None
            raise

    def find_by_email(self, email: str) -> dict[str, Any] | None:
        """Localiza usuário pelo e-mail.

        Em alguns servidores o filtro `users.list?query=` é ignorado; nesse caso
        tenta o username = parte local do e-mail (ex.: diego.couto).
        """
        email_norm = email.strip().lower()
        query = quote(f'{{"emails.address":"{email_norm}"}}')
        try:
            data = self._request("GET", f"users.list?query={query}&count=20")
            users = data.get("users") or []
            for user in users:
                emails = user.get("emails") or []
                for item in emails:
                    addr = (item.get("address") or "").strip().lower()
                    if addr == email_norm:
                        return user
        except RocketChatError:
            pass

        # Fallback: username = local-part do e-mail (contas antigas)
        local = email_norm.split("@", 1)[0].strip()
        if local:
            user = self.find_by_username(local)
            if user:
                for item in user.get("emails") or []:
                    addr = (item.get("address") or "").strip().lower()
                    if addr == email_norm:
                        return user
        return None

    @retry(
        retry=retry_if_exception_type(RocketChatError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def create_user(
        self,
        *,
        name: str,
        email: str,
        username: str,
        password: str,
        require_password_change: bool = True,
        verified: bool = True,
        active: bool = True,
        cpf: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "email": email,
            "username": username,
            "password": password,
            "active": active,
            "verified": verified,
            "requirePasswordChange": require_password_change,
            "sendWelcomeEmail": False,
            # Evita entrar automaticamente em canais default (#general etc.)
            "joinDefaultChannels": False,
        }
        if cpf:
            body["customFields"] = {"cpf": cpf}
        data = self._request("POST", "users.create", json=body)
        user = data.get("user")
        if not user:
            raise RocketChatError("users.create sem user na resposta", body=str(data))
        return user

    def delete_user(self, user_id: str) -> None:
        self._request("POST", "users.delete", json={"userId": user_id, "confirmRelinquish": True})

    def forgot_password(self, email: str) -> dict[str, Any]:
        """Dispara e-mail de reset via SMTP do Rocket.Chat (não envia a senha em texto)."""
        url = f"{self.base_url}/api/v1/users.forgotPassword"
        resp = self.session.post(url, json={"email": email}, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RocketChatError("Falha forgotPassword", resp.status_code, resp.text)
        try:
            return resp.json()
        except ValueError as exc:
            raise RocketChatError("JSON inválido forgotPassword", resp.status_code, resp.text) from exc

    def get_setting(self, setting_id: str) -> Any:
        data = self._request("GET", f"settings/{setting_id}")
        return data.get("value")

    def update_setting(self, setting_id: str, value: Any) -> dict[str, Any]:
        return self._request("POST", f"settings/{setting_id}", json={"value": value})

    def find_room_by_name(self, room_name: str) -> dict[str, Any] | None:
        """Localiza canal público ou grupo privado pelo nome."""
        name = (room_name or "").strip()
        if not name:
            return None
        # Preferência: rooms.adminRooms (encontra públicos e privados)
        try:
            data = self._request("GET", f"rooms.adminRooms?filter={quote(name)}&count=50")
            for room in data.get("rooms") or []:
                if (room.get("name") or "").strip().lower() == name.lower():
                    return room
        except RocketChatError:
            pass
        for path, key in (
            (f"channels.info?roomName={quote(name)}", "channel"),
            (f"groups.info?roomName={quote(name)}", "group"),
        ):
            try:
                data = self._request("GET", path)
                room = data.get(key)
                if room:
                    return room
            except RocketChatError:
                continue
        return None

    def find_room_by_id(self, room_id: str) -> dict[str, Any] | None:
        rid = (room_id or "").strip()
        if not rid:
            return None
        for path, key in (
            (f"channels.info?roomId={rid}", "channel"),
            (f"groups.info?roomId={rid}", "group"),
            (f"rooms.info?roomId={rid}", "room"),
        ):
            try:
                data = self._request("GET", path)
                room = data.get(key)
                if room:
                    return room
            except RocketChatError:
                continue
        return None

    def resolve_default_room(self, room_id: str = "", room_name: str = "SNAS") -> dict[str, Any]:
        """
        Resolve o canal padrão de entrada dos usuários provisionados.
        Prioridade: room_name (padrão SNAS). Se room_id for informado, valida consistência.
        """
        name_esperado = (room_name or "SNAS").strip() or "SNAS"
        room = self.find_room_by_name(name_esperado)
        if not room and room_id:
            room = self.find_room_by_id(room_id)
        if not room:
            raise RocketChatError(f"Canal/grupo '{name_esperado}' não encontrado no Rocket.Chat")

        nome = (room.get("name") or "").strip()
        if nome.lower() != name_esperado.lower():
            raise RocketChatError(
                f"Sala resolvida é '{nome}', mas o canal esperado é '{name_esperado}'. "
                f"Atualize RC_ROOM_ID ou RC_ROOM_NAME no .env."
            )

        rid = room.get("_id")
        if not rid:
            raise RocketChatError(f"Sala '{name_esperado}' sem _id")

        if room_id and room_id != rid:
            raise RocketChatError(
                f"RC_ROOM_ID '{room_id}' não corresponde a '{name_esperado}' "
                f"(id real={rid}). Atualize o .env."
            )
        return room

    def invite_to_channel(self, room_id: str, user_id: str, room_type: str | None = None) -> None:
        if not room_id or not user_id:
            return

        # t = c público, p privado
        order = ("groups", "channels") if room_type == "p" else ("channels", "groups")
        last_exc: RocketChatError | None = None
        for kind in order:
            try:
                self._request("POST", f"{kind}.invite", json={"roomId": room_id, "userId": user_id})
                return
            except RocketChatError as exc:
                body = (exc.body or "").lower()
                if any(x in body for x in ("already", "exist", "já", "ja ")):
                    return
                last_exc = exc
        if last_exc:
            logger.warning("Falha ao convidar user_id=%s room=%s: %s", user_id, room_id, last_exc)


def sleep_ms(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)
