from __future__ import annotations

# Usernames que NUNCA podem ser excluídos via este projeto.
# Inclui contas de sistema e o admin do .env (adicionado dinamicamente).
PROTECTED_USERNAMES_BASE: frozenset[str] = frozenset(
    {
        "admin",
        "admin_mds",
        "rocket.cat",
        "bot",
        "system",
    }
)


def protected_usernames(admin_user: str | None = None) -> frozenset[str]:
    names = set(PROTECTED_USERNAMES_BASE)
    if admin_user and admin_user.strip():
        names.add(admin_user.strip().lower())
    return frozenset(names)


def is_protected_username(username: str, admin_user: str | None = None) -> bool:
    return username.strip().lower() in protected_usernames(admin_user)


def assert_safe_to_delete(username: str, admin_user: str | None = None) -> None:
    u = (username or "").strip()
    if not u:
        raise ValueError("Username obrigatório para exclusão.")
    if is_protected_username(u, admin_user):
        raise PermissionError(
            f"Exclusão bloqueada: '{u}' é uma conta protegida "
            "(admin/sistema). Informe outro username."
        )
