from __future__ import annotations

import secrets
import string


ALPHABET = string.ascii_letters + string.digits + "!@#$%&*"


def gerar_senha(tamanho: int = 14) -> str:
    """Gera senha aleatória com letras, números e símbolos."""
    if tamanho < 10:
        raise ValueError("Senha deve ter ao menos 10 caracteres")

    # Garante pelo menos 1 de cada classe
    obrigatorio = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%&*"),
    ]
    resto = [secrets.choice(ALPHABET) for _ in range(tamanho - len(obrigatorio))]
    chars = obrigatorio + resto
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
