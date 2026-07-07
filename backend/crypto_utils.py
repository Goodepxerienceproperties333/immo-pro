"""iter90av : Fernet-based encryption for secret storage (SMTP passwords,
Azure client secrets, etc.).

Usage:
    from crypto_utils import encrypt_secret, decrypt_secret, is_encrypted

    encrypted = encrypt_secret("my-secret")   # -> "enc:v1:gAAAAA..."
    plain = decrypt_secret(encrypted)         # -> "my-secret"

The encryption key is loaded from env EMAIL_CONFIG_KEY. If not set at import
time, a warning is logged and encrypt/decrypt raise RuntimeError when called
(fail fast).

Key format: 32 url-safe base64 bytes (as produced by `Fernet.generate_key()`).
Generate a new key locally with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("crypto_utils")

_PREFIX = "enc:v1:"
_key = os.environ.get("EMAIL_CONFIG_KEY", "").strip()
_fernet: Optional[Fernet] = None
if _key:
    try:
        _fernet = Fernet(_key.encode() if isinstance(_key, str) else _key)
    except Exception as e:  # noqa: BLE001
        logger.error("EMAIL_CONFIG_KEY invalide (%s) - encryption desactive", e)
        _fernet = None
else:
    logger.warning(
        "EMAIL_CONFIG_KEY absent - les secrets syndic ne seront PAS chiffres. "
        "Generer avec : python -c 'from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())' puis exporter dans backend/.env"
    )


def is_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_secret(plain: Optional[str]) -> str:
    """Chiffre une chaine. Retourne "" si input est vide/None. Retourne le token
    prefixe (enc:v1:...) pour distinguer d'un secret en clair legacy."""
    if not plain:
        return ""
    if is_encrypted(plain):
        return plain  # deja chiffre, idempotent
    if _fernet is None:
        raise RuntimeError(
            "EMAIL_CONFIG_KEY non defini - impossible de chiffrer un secret. "
            "Configurer la variable d'env avant d'enregistrer des credentials."
        )
    token = _fernet.encrypt(plain.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_secret(value: Optional[str]) -> str:
    """Dechiffre un token prefixe enc:v1:... Retourne "" si vide/None. Si
    l'input n'est PAS prefixe (secret legacy en clair), le retourne tel quel."""
    if not value:
        return ""
    if not is_encrypted(value):
        return value  # legacy clear-text (retro-compat)
    if _fernet is None:
        raise RuntimeError(
            "EMAIL_CONFIG_KEY non defini - impossible de dechiffrer un secret. "
            "Configurer la variable d'env qui a chiffre ces donnees."
        )
    token = value[len(_PREFIX):]
    try:
        return _fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError(
            "Token de secret invalide (cle EMAIL_CONFIG_KEY differente ou "
            "donnees corrompues)"
        ) from e


def redact(value: Optional[str], keep: int = 4) -> str:
    """Masque un secret pour l'UI. Ex: "abcdef1234" -> "****1234"."""
    if not value:
        return ""
    if is_encrypted(value):
        # On ne dechiffre pas juste pour redacter - on montre "configure"
        return "********"
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
