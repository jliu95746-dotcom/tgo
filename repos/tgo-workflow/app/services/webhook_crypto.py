"""Versioned authenticated encryption for source-bound webhook credentials."""

import base64
import hashlib
import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.config import settings
from app.services.event_keyring import (
    EventKeyring,
    EventKeyringError,
    parse_event_keyring,
)
from app.services.webhook_protocol import KEY_ID


class WebhookCryptoError(RuntimeError):
    """Safe diagnostics only; no ciphertext, key or plaintext values."""


def webhook_keyring() -> EventKeyring:
    try:
        result = parse_event_keyring(
            settings.WORKFLOW_WEBHOOK_KEYS,
            settings.WORKFLOW_WEBHOOK_ACTIVE_KEY_ID,
            "",
        )
        if not result.configured or result.active_id is None:
            raise EventKeyringError()
        return result
    except EventKeyringError:
        raise WebhookCryptoError("外部触发凭证保管密钥未配置或无效。") from None


def ensure_credential_crypto() -> None:
    webhook_keyring()


def _cipher(ring: EventKeyring, key_id: str) -> Fernet:
    try:
        material = (
            b"tgo-webhook-credential:v1\x00"
            + key_id.encode("ascii")
            + b"\x00"
            + ring.secret(key_id).encode("utf-8")
        )
        return Fernet(
            base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        )
    except (EventKeyringError, UnicodeError):
        raise WebhookCryptoError("外部触发凭证所需保管密钥不可用。") from None


def _scope(hook_id: UUID, credential_revision: int, key_id: str) -> str:
    if (
        not isinstance(hook_id, UUID)
        or type(credential_revision) is not int
        or not 1 <= credential_revision <= 2147483647
        or KEY_ID.fullmatch(key_id) is None
    ):
        raise WebhookCryptoError("外部触发凭证范围无效。")
    return f"{hook_id}\n{credential_revision}\n{key_id}\n"


def _check_secret(secret: SecretStr) -> str:
    value = secret.get_secret_value()
    if not value.isascii() or not 32 <= len(value) <= 512:
        raise WebhookCryptoError("外部触发签名凭证格式无效。")
    return value


def seal_credential(
    *,
    hook_id: UUID,
    credential_revision: int,
    key_id: str,
    secret: SecretStr,
) -> str:
    ring = webhook_keyring()
    master_id = ring.active_id
    if master_id is None:
        raise WebhookCryptoError("外部触发凭证保管密钥未配置。")
    scope = _scope(hook_id, credential_revision, key_id)
    plaintext = json.dumps(
        {"scope": scope, "secret": _check_secret(secret)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    token = (
        _cipher(ring, master_id).encrypt(plaintext.encode()).decode("ascii")
    )
    return f"wh1:{master_id}:{token}"


def open_credential(
    *,
    hook_id: UUID,
    credential_revision: int,
    key_id: str,
    encrypted: str,
) -> SecretStr:
    scope = _scope(hook_id, credential_revision, key_id)
    try:
        if len(encrypted) > 8192:
            raise ValueError()
        parts = encrypted.split(":", 2)
        if len(parts) != 3 or parts[0] != "wh1" or parts[1] == "legacy":
            raise ValueError()
        plaintext = _cipher(webhook_keyring(), parts[1]).decrypt(
            parts[2].encode("ascii")
        )
        payload = json.loads(plaintext)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"scope", "secret"}
            or payload["scope"] != scope
            or not isinstance(payload["secret"], str)
        ):
            raise ValueError()
        secret = SecretStr(payload["secret"])
        _check_secret(secret)
        return secret
    except (InvalidToken, ValueError, UnicodeError):
        raise WebhookCryptoError("外部触发凭证无法解密或范围不一致。") from None
