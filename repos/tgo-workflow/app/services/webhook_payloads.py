"""Domain-separated input encryption and retained, versioned replay proofs."""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import JsonValue, TypeAdapter

from app.schemas.webhook_delivery import WebhookPayloadScope
from app.services.event_keyring import EventKeyring, EventKeyringError
from app.services.webhook_crypto import WebhookCryptoError, webhook_keyring

_object = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class FrozenWebhookPayload:
    key_id: str
    receipt: str
    ciphertext: str = field(repr=False)


def _material(ring: EventKeyring, key_id: str, purpose: bytes) -> bytes:
    try:
        return hashlib.sha256(
            b"tgo-webhook:"
            + purpose
            + b":v1\x00"
            + key_id.encode("ascii")
            + b"\x00"
            + ring.secret(key_id).encode("utf-8")
        ).digest()
    except (EventKeyringError, UnicodeError):
        raise WebhookCryptoError("外部通知所需历史保管密钥不可用。") from None


def _canonical(data: dict[str, JsonValue]) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _receipt(
    ring: EventKeyring,
    key_id: str,
    hook_id: UUID,
    event_key: str,
    data: dict[str, JsonValue],
) -> str:
    return hmac.new(
        _material(ring, key_id, b"replay-proof"),
        str(hook_id).encode()
        + b"\x00"
        + event_key.encode()
        + b"\x00"
        + _canonical(data),
        hashlib.sha256,
    ).hexdigest()


def freeze_payload(
    scope: WebhookPayloadScope,
    data: dict[str, JsonValue],
) -> FrozenWebhookPayload:
    ring = webhook_keyring()
    key_id = ring.active_id
    if key_id is None:
        raise WebhookCryptoError("外部通知保管密钥未配置。")
    receipt = _receipt(ring, key_id, scope.hook_id, scope.event_key, data)
    plaintext = _canonical(
        {"scope": scope.model_dump(mode="json"), "data": data}
    )
    cipher = Fernet(
        base64.urlsafe_b64encode(_material(ring, key_id, b"payload"))
    )
    token = cipher.encrypt(plaintext).decode("ascii")
    return FrozenWebhookPayload(key_id, receipt, f"wp1:{key_id}:{token}")


def matches_payload(
    hook_id: UUID,
    event_key: str,
    data: dict[str, JsonValue],
    key_id: str,
    receipt: str,
) -> bool:
    expected = _receipt(webhook_keyring(), key_id, hook_id, event_key, data)
    return hmac.compare_digest(expected, receipt)


def open_payload(
    scope: WebhookPayloadScope,
    encrypted: str,
) -> dict[str, JsonValue]:
    try:
        parts = encrypted.split(":", 2)
        if len(encrypted) > 200000 or len(parts) != 3 or parts[0] != "wp1":
            raise ValueError()
        material = _material(webhook_keyring(), parts[1], b"payload")
        cipher = Fernet(base64.urlsafe_b64encode(material))
        envelope = json.loads(cipher.decrypt(parts[2].encode("ascii")))
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"scope", "data"}
            or envelope["scope"] != scope.model_dump(mode="json")
        ):
            raise ValueError()
        return _object.validate_python(envelope["data"])
    except (InvalidToken, ValueError, UnicodeError, TypeError):
        raise WebhookCryptoError("外部通知输入无法核验或来源范围不一致。") from None
