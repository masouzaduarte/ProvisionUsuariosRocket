from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class UsuarioEntrada:
    linha: int
    nome: str
    email: str
    username: str
    cpf: str = ""


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def username_from_email(email: str) -> str:
    local = email.split("@", 1)[0].strip().lower()
    local = _strip_accents(local)
    local = re.sub(r"[^a-z0-9._-]", ".", local)
    local = re.sub(r"[.]+", ".", local).strip(".")
    return local[:30] or "usuario"


def username_from_cpf(cpf: str) -> str:
    """Username no Rocket = CPF só com dígitos (11)."""
    return normalizar_cpf(cpf)


def normalizar_username(raw: str | None, email: str) -> str:
    """Legado: se raw vier preenchido usa ele; senão deriva do e-mail."""
    if raw and raw.strip():
        u = _strip_accents(raw.strip().lower())
        u = re.sub(r"[^a-z0-9._-]", ".", u)
        u = re.sub(r"[.]+", ".", u).strip(".")
        return u[:30]
    return username_from_email(email)


def validar_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


CPF_ZEROS = "00000000000"


def normalizar_cpf(cpf: str | None) -> str:
    if not cpf:
        return ""
    return re.sub(r"\D", "", cpf.strip())


def nome_valido(nome: str | None) -> bool:
    """Nome obrigatório: não vazio e não só dígitos/pontuação."""
    n = (nome or "").strip()
    if not n:
        return False
    letras = re.sub(r"[^A-Za-zÀ-ÿ]", "", n)
    return len(letras) >= 2


def validar_cpf(cpf: str | None) -> bool:
    """Valida CPF (11 dígitos + dígitos verificadores). Rejeita 00000000000 e sequências iguais."""
    digits = normalizar_cpf(cpf)
    if not digits:
        return False
    if digits == CPF_ZEROS:
        return False
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    def _dv(base: str) -> str:
        soma = sum(int(d) * w for d, w in zip(base, range(len(base) + 1, 1, -1)))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    return digits[-2:] == _dv(digits[:9]) + _dv(digits[:10])
