"""Validate a shadow source against the server's customer IM message."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from app.schemas.shadow_review import ShadowSourceProof
from app.schemas.wukongim import WuKongIMMessage
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


class ShadowIMLookup(Protocol):
    enabled: bool

    async def get_message_by_client_msg_no(
        self,
        *,
        channel_id: str,
        channel_type: int,
        client_msg_no: str,
        raise_on_error: bool,
    ) -> WuKongIMMessage | None:
        ...


def shadow_message_matches(
    message: WuKongIMMessage,
    *,
    visitor_id: UUID,
    source_message_id: str,
    source_client_msg_no: str,
    customer_message: str,
    checked_at: datetime,
) -> bool:
    """Apply the same provenance rules to generation and later review reads."""
    channel_id = build_visitor_channel_id(visitor_id)
    payload = message.payload
    extra = payload.get("extra") if isinstance(payload, dict) else None
    platform_message_id = (
        extra.get("message_id") if isinstance(extra, dict) else None
    )
    content = payload.get("content") if isinstance(payload, dict) else None
    now_seconds = int(checked_at.timestamp())
    return (
        message.channel_id == channel_id
        and message.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE
        and message.from_uid == channel_id
        and message.client_msg_no == source_client_msg_no
        and type(message.timestamp) is int
        and 0 < message.timestamp <= now_seconds + 30
        and type(message.message_id) is int
        and message.message_id > 0
        and type(message.message_seq) is int
        and message.message_seq > 0
        and isinstance(payload, dict)
        and payload.get("type") == 1
        and isinstance(content, str)
        and content.strip() == customer_message.strip()
        and source_message_id in (
            source_client_msg_no,
            str(message.message_id),
            platform_message_id,
        )
    )


async def verify_shadow_source(
    *,
    im_client: ShadowIMLookup,
    visitor_id: UUID,
    source_message_id: str | None,
    source_client_msg_no: str | None,
    customer_message: str,
    message_type: int,
) -> ShadowSourceProof:
    """Keep missing IM evidence unverified while allowing a staff draft."""
    checked_at = datetime.now(timezone.utc)

    def unknown(status: str) -> ShadowSourceProof:
        return ShadowSourceProof.model_validate(
            {
                "status": status,
                "checked_at": checked_at,
            }
        )

    if message_type != 1:
        return unknown("unsupported")
    if not source_message_id or not source_client_msg_no:
        return unknown("unverified")
    if not im_client.enabled:
        return unknown("unavailable")
    channel_id = build_visitor_channel_id(visitor_id)
    try:
        message = await im_client.get_message_by_client_msg_no(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            client_msg_no=source_client_msg_no,
            raise_on_error=True,
        )
    except Exception:
        return unknown("unavailable")
    if message is None:
        return unknown("missing")
    if not shadow_message_matches(
        message,
        visitor_id=visitor_id,
        source_message_id=source_message_id,
        source_client_msg_no=source_client_msg_no,
        customer_message=customer_message,
        checked_at=checked_at,
    ):
        return unknown("mismatch")
    content = message.payload["content"]
    assert isinstance(content, str)
    return ShadowSourceProof(
        status="verified",
        checked_at=checked_at,
        channel_id=message.channel_id,
        channel_type=message.channel_type,
        client_msg_no=message.client_msg_no,
        message_id=str(message.message_id),
        message_seq=message.message_seq,
        message_timestamp=message.timestamp,
        content_sha256=hashlib.sha256(content.strip().encode()).hexdigest(),
    )
