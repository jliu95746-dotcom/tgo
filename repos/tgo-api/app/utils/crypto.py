"""Symmetric encryption utilities for protecting secrets at rest.

Uses Fernet (symmetric authenticated encryption) with a key derived from settings.SECRET_KEY.
This enables reversible encryption for fields like API keys.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


# Derive a 32-byte urlsafe key for Fernet from the application's SECRET_KEY
# Note: Rotating SECRET_KEY will invalidate existing ciphertexts.
# If key rotation is required, implement a keyring with key IDs.
_DEF_FERNET_KEY: bytes = base64.urlsafe_b64encode(
    hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
)


def _get_fernet() -> Fernet:
    return Fernet(_DEF_FERNET_KEY)


def encrypt_str(plain: str | None) -> str:
    """Encrypt a string and return urlsafe base64 token."""
    if plain is None:
        return ""
    f = _get_fernet()
    token: bytes = f.encrypt(plain.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(token: str) -> Optional[str]:
    """Decrypt a token back to string. Returns None if invalid/corrupted."""
    if not token:
        return None
    f = _get_fernet()
    try:
        data = f.decrypt(token.encode("utf-8"))
        return data.decode("utf-8")
    except (InvalidToken, Exception):
        return None


def mask_secret(plain: Optional[str]) -> Optional[str]:
    """Return a masked representation of a secret, revealing only last 4 chars.
    If plain is None or empty, returns None.
    """
    if not plain:
        return None
    n = len(plain)
    if n <= 4:
        return "*" * n
    return "*" * (n - 4) + plain[-4:]
