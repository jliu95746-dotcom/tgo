"""Keep provider reply targets on their owning service and encrypted at rest."""

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Platform
from app.db.turn_context import CustomerTurnContext
from app.domain.entities import NormalizedMessage
from app.domain.services.conversation_scope import upstream_scope


def context_key() -> bytes:
    secret = settings.saas_internal_token
    if secret is None:
        raise RuntimeError(
            "Internal service credentials are required for durable channel replies"
        )
    return hashlib.sha256(
        b"customer-turn-context:v1:" + secret.get_secret_value().encode()
    ).digest()


def _clean(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _clean(v)
            for k, v in value.items()
            if k
            not in {
                "bot_token",
                "app_secret",
                "app_token",
                "signing_secret",
                "platform_api_key",
                "api_key",
                "smtp_password",
            }
        }
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def seal(message: NormalizedMessage) -> str:
    body = message.model_dump(exclude={"platform_api_key"})
    body["extra"] = _clean(body.get("extra"))
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    if len(data) > 131072:
        raise ValueError("Channel delivery context is too large")
    nonce = os.urandom(12)
    return base64.b64encode(
        nonce
        + AESGCM(context_key()).encrypt(nonce, data, str(message.platform_id).encode())
    ).decode()


def unseal(row: CustomerTurnContext, platform: Platform) -> NormalizedMessage:
    raw = base64.b64decode(row.encrypted_payload, validate=True)
    content = AESGCM(context_key()).decrypt(
        raw[:12], raw[12:], str(platform.id).encode()
    )
    value = json.loads(content)
    value["platform_api_key"] = platform.api_key
    message = NormalizedMessage.model_validate(value)
    if str(message.platform_id) != str(platform.id):
        raise ValueError("Delivery context belongs to a different platform")
    return message


async def capture(
    db: AsyncSession, message: NormalizedMessage, *, commit: bool = True
) -> None:
    source = (message.extra or {}).get("message_id")
    if not isinstance(source, str) or not source:
        raise ValueError("Durable channel messages require a stable source ID")
    platform = await db.scalar(
        select(Platform).where(Platform.id == UUID(message.platform_id))
    )
    if (
        platform is None
        or not platform.is_active
        or platform.api_key != message.platform_api_key
    ):
        raise ValueError("Invalid channel identity")
    scope = upstream_scope(message)
    actor = hashlib.sha256(
        json.dumps(
            {
                **scope,
                "message_id": None,
                "sender": message.from_uid,
                "platform": message.platform_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if db.get_bind().dialect.name == "postgresql":
        number = int.from_bytes(bytes.fromhex(actor[:16]), "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})
    existing = await db.get(CustomerTurnContext, (platform.id, source))
    extra = message.extra or {}
    stable = {
        "content": message.content,
        "msg_type": extra.get("msg_type", 1),
        "media_file_id": extra.get("media_file_id"),
        "media_pending": extra.get("media_pending", False),
        "media_error": extra.get("media_error"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(stable, sort_keys=True).encode()
    ).hexdigest()
    if existing is not None:
        previous = unseal(existing, platform)
        if previous.from_uid != message.from_uid or upstream_scope(
            previous
        ) != upstream_scope(message):
            raise ValueError("Source message cannot change its sender or conversation")
        if extra.get("media_pending") and not (previous.extra or {}).get(
            "media_pending"
        ):
            # Retried source callbacks must not erase a completed media upload.
            return
        if existing.fingerprint != fingerprint and not (previous.extra or {}).get(
            "media_pending"
        ):
            raise ValueError("Registered source content cannot be replaced")
        existing.encrypted_payload = seal(message)
        existing.has_pending_media = bool(
            extra.get("media_pending") and extra.get("incoming_media")
        )
        if existing.fingerprint != fingerprint:
            existing.fingerprint, existing.registered_at = fingerprint, None
            existing.due_at = datetime.now(timezone.utc)
        if commit:
            await db.commit()
        else:
            await db.flush()
        return
    statement = (
        insert(CustomerTurnContext)
        .values(
            platform_id=platform.id,
            source_message_id=source,
            encrypted_payload=seal(message),
            fingerprint=fingerprint,
            actor_key=actor,
            has_pending_media=bool(
                extra.get("media_pending") and extra.get("incoming_media")
            ),
        )
        .on_conflict_do_nothing()
    )
    await db.execute(statement)
    if commit:
        await db.commit()
    else:
        await db.flush()
