"""Capture customer WeCom parts before media download or serial listeners."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Platform, WeComInbox
from app.domain.entities import NormalizedMessage
from app.domain.services.turn_context import capture


async def capture_inbox(db: AsyncSession, record: WeComInbox) -> None:
    if record.msg_type not in {"text", "image", "voice"}:
        return
    source = record.source_type or "wecom_kf"
    if source not in {"wecom_kf", "wecom_bot"}:
        return
    raw = record.raw_payload or {}
    original = raw.get("kf_sync_msg") or {}
    if (
        source == "wecom_kf"
        and "origin" in original
        and original["origin"] not in (3, "3")
    ):
        return
    platform = await db.scalar(
        select(Platform).where(Platform.id == record.platform_id)
    )
    if platform is None or not platform.is_active or not platform.api_key:
        return
    colleague = (
        True if record.is_from_colleague is None else bool(record.is_from_colleague)
    )
    context = {"source_type": source, "is_from_colleague": colleague}
    if source == "wecom_kf":
        context.update(
            open_kfid=original.get("open_kfid")
            or raw.get("open_kfid")
            or record.open_kfid,
            external_userid=original.get("external_userid")
            or raw.get("external_userid")
            or record.from_user,
        )
    else:
        context.update(
            response_url=raw.get("response_url") or "",
            chat_id=raw.get("chat_id") or record.open_kfid or "",
            chat_type=raw.get("chat_type") or "",
        )
    media = record.msg_type in {"image", "voice"}
    failed = media and record.status in {"media_failed", "unsupported_media"}
    await capture(
        db,
        NormalizedMessage(
            source="wecom",
            from_uid=record.from_user,
            content="" if media else record.content or "",
            platform_api_key=platform.api_key,
            platform_type=platform.type,
            platform_id=str(platform.id),
            extra={
                "message_id": record.message_id,
                "msg_type": {"text": 1, "image": 2, "voice": 4}[record.msg_type],
                "media_pending": media and not failed,
                "media_error": "download_failed" if failed else None,
                "wecom": context,
            },
        ),
        commit=False,
    )
