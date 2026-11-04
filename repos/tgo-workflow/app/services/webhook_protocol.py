"""Version-one external webhook authentication, with bounded JSON parsing.

This module neither registers hooks nor deduplicates or executes deliveries.
Ingress supplies the server-owned hook, credential revision and key,
and persists acceptance separately.
"""

from collections.abc import Mapping
import hashlib
import hmac
import json
import math
import re
import time
from typing import Literal, NoReturn
from uuid import UUID

from pydantic import SecretStr

from app.schemas.webhook_delivery import (
    VerifiedWebhookDelivery,
    WebhookDeliveryEnvelope,
)

MAX_BODY_BYTES = 65536
SIGNATURE_WINDOW_SECONDS = 300
MAX_DEPTH = 8
MAX_NODES = 1024
MAX_OBJECT_KEYS = 128
MAX_TEXT_LENGTH = 4096
EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
TIMESTAMP = re.compile(r"0|[1-9][0-9]{0,11}")
SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")
HEADER_NAMES = {"timestamp", "event_id", "key_id", "signature"}
RESERVED_NAMES = frozenset(
    {
        "project_id",
        "actor_staff_id",
        "workflow_id",
        "release_id",
        "release_digest",
        "expected_release_id",
        "expected_release_digest",
        "source_binding",
        "execution_id",
        "execution_mode",
        "source_channel",
        "source_message_id",
        "workflow_lineage",
        "subscription_id",
        "session_id",
        "delivery_id",
        "hook_id",
        "credential_revision",
        "binding_revision",
        "key_id",
        "constructor",
        "prototype",
    }
)
ProtocolCode = Literal[
    "webhook_configuration_invalid",
    "webhook_headers_invalid",
    "webhook_signature_expired",
    "webhook_signature_invalid",
    "webhook_body_too_large",
    "webhook_content_type_invalid",
    "webhook_payload_invalid",
]


class WebhookProtocolError(ValueError):
    def __init__(self, code: ProtocolCode, status_code: int):
        self.code = code
        self.status_code = status_code
        super().__init__(
            {
                "webhook_configuration_invalid": "外部触发的服务配置无效。",
                "webhook_headers_invalid": "外部触发签名头格式无效。",
                "webhook_signature_expired": "外部触发签名不在有效时间内。",
                "webhook_signature_invalid": "外部触发签名无法核验。",
                "webhook_body_too_large": "外部触发数据超过大小限制。",
                "webhook_content_type_invalid": "外部触发仅接受 UTF-8 JSON。",
                "webhook_payload_invalid": "外部触发数据格式或字段无效。",
            }[code]
        )


def signing_message(
    hook_id: UUID,
    credential_revision: int,
    key_id: str,
    timestamp: str,
    event_id: str,
    body: bytes,
) -> bytes:
    return (
        f"TGO-WEBHOOK-V1\n{hook_id}\n{credential_revision}\n{key_id}\n"
        f"{timestamp}\n{event_id}\n"
    ).encode("ascii") + body


def _invalid_payload() -> NoReturn:
    raise WebhookProtocolError("webhook_payload_invalid", 422)


def _unique_object_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in items:
        if name in result:
            _invalid_payload()
        result[name] = value
    return result


def _reject_non_finite_constant(value: str) -> NoReturn:
    _invalid_payload()


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        _invalid_payload()
    return result


def _check_text(value: str, limit: int) -> None:
    if len(value) > limit or "\x00" in value:
        _invalid_payload()
    value.encode("utf-8")


def _check_structure(value: object) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            _invalid_payload()
        if isinstance(item, dict):
            if len(item) > MAX_OBJECT_KEYS:
                _invalid_payload()
            for name, child in item.items():
                if (
                    not isinstance(name, str)
                    or not 1 <= len(name) <= 128
                    or name.startswith("_")
                    or name.casefold() in RESERVED_NAMES
                ):
                    _invalid_payload()
                _check_text(name, 128)
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_NODES:
                _invalid_payload()
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            _check_text(item, MAX_TEXT_LENGTH)


def _parse_payload(body: bytes) -> WebhookDeliveryEnvelope:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_reject_non_finite_constant,
            parse_float=_finite_float,
        )
        _check_structure(value)
        return WebhookDeliveryEnvelope.model_validate(value)
    except (ValueError, RecursionError):
        raise WebhookProtocolError("webhook_payload_invalid", 422) from None


def _require_json_content_type(value: str | None) -> None:
    if value is None:
        raise WebhookProtocolError("webhook_content_type_invalid", 415)
    parts = [part.strip() for part in value.split(";")]
    valid = parts[0].lower() == "application/json" and len(parts) <= 2
    if len(parts) == 2:
        parameter = parts[1].split("=")
        valid = (
            valid
            and len(parameter) == 2
            and (
                parameter[0].strip().lower() == "charset"
                and parameter[1].strip().lower() in {"utf-8", '"utf-8"'}
            )
        )
    if not valid:
        raise WebhookProtocolError("webhook_content_type_invalid", 415)


def verify_delivery(
    *,
    hook_id: UUID,
    credential_revision: int,
    key_id: str,
    secret: SecretStr | None,
    body: bytes,
    headers: Mapping[str, str | None],
    content_type: str | None,
    now: int | None = None,
) -> VerifiedWebhookDelivery:
    current = int(time.time()) if now is None else now
    key = secret.get_secret_value() if secret is not None else ""
    if (
        not isinstance(hook_id, UUID)
        or type(credential_revision) is not int
        or not 1 <= credential_revision <= 2147483647
        or not isinstance(key_id, str)
        or not KEY_ID.fullmatch(key_id)
        or not key.isascii()
        or not 32 <= len(key) <= 512
        or type(current) is not int
        or current < 0
    ):
        raise WebhookProtocolError("webhook_configuration_invalid", 503)
    if len(body) > MAX_BODY_BYTES:
        raise WebhookProtocolError("webhook_body_too_large", 413)
    _require_json_content_type(content_type)
    if set(headers) != HEADER_NAMES or any(
        not isinstance(value, str) for value in headers.values()
    ):
        raise WebhookProtocolError("webhook_headers_invalid", 403)
    timestamp, event_id = headers["timestamp"], headers["event_id"]
    received_key, signature = headers["key_id"], headers["signature"]
    if (
        timestamp is None
        or event_id is None
        or received_key is None
        or signature is None
        or not TIMESTAMP.fullmatch(timestamp)
        or not EVENT_ID.fullmatch(event_id)
        or not KEY_ID.fullmatch(received_key)
        or not SIGNATURE.fullmatch(signature)
    ):
        raise WebhookProtocolError("webhook_headers_invalid", 403)
    if abs(current - int(timestamp)) > SIGNATURE_WINDOW_SECONDS:
        raise WebhookProtocolError("webhook_signature_expired", 403)
    expected = hmac.new(
        key.encode("ascii"),
        signing_message(
            hook_id,
            credential_revision,
            received_key,
            timestamp,
            event_id,
            body,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(
        received_key, key_id
    ) or not hmac.compare_digest(signature[7:], expected):
        raise WebhookProtocolError("webhook_signature_invalid", 403)
    payload = _parse_payload(body)
    return VerifiedWebhookDelivery(
        hook_id=hook_id,
        credential_revision=credential_revision,
        key_id=received_key,
        event_id=event_id,
        data=payload.data,
    )
