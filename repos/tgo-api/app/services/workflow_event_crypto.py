"""Versioned encryption for frozen workflow event inputs only."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.services.event_keyring import parse_event_keyring


def ensure_event_payload_crypto() -> None:
    ring = parse_event_keyring(
        settings.WORKFLOW_EVENT_DELIVERY_KEYS,
        settings.WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID,
        settings.SECRET_KEY,
    )
    ring.secret(ring.active_id)


def _fernet(secret: str, key_id: str | None) -> Fernet:
    material = secret.encode("utf-8")
    if key_id is not None:
        material = (
            b"tgo-workflow-event-delivery:v1\x00"
            + key_id.encode("ascii")
            + b"\x00"
            + material
        )
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_event_payload(plaintext: str) -> str:
    ring = parse_event_keyring(
        settings.WORKFLOW_EVENT_DELIVERY_KEYS,
        settings.WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID,
        settings.SECRET_KEY,
    )
    key_id = ring.active_id
    cipher = _fernet(ring.secret(key_id), key_id)
    token = cipher.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return token if key_id is None else f"ev1:{key_id}:{token}"


def decrypt_event_payload(encrypted: str) -> str:
    ring = parse_event_keyring(
        settings.WORKFLOW_EVENT_DELIVERY_KEYS,
        settings.WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID,
        settings.SECRET_KEY,
    )
    key_id: str | None = None
    token = encrypted
    if ":" in encrypted:
        parts = encrypted.split(":", 2)
        if (
            len(parts) != 3
            or parts[0] != "ev1"
            or not parts[1]
            or len(parts[1]) > 32
            or parts[1] == "legacy"
        ):
            raise ValueError("事件输入加密格式无效")
        _, key_id, token = parts
    cipher = _fernet(ring.secret(key_id), key_id)
    try:
        return cipher.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        raise ValueError("事件输入加密校验失败") from None
