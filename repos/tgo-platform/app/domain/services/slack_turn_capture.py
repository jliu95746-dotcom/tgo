"""Persist all Slack parts before downloading files or waiting on generation."""

from app.domain.entities import NormalizedMessage
from app.domain.services.turn_context import capture


async def capture_event(sessions, platform, event: dict[str, object]) -> None:
    if event.get("bot_id") or event.get("subtype") in {
        "bot_message",
        "message_changed",
        "message_deleted",
    }:
        return
    user, channel, timestamp = event.get("user"), event.get("channel"), event.get("ts")
    if (
        not isinstance(user, str)
        or not isinstance(channel, str)
        or not isinstance(timestamp, str)
        or not all((user, channel, timestamp))
    ):
        return
    raw_files = event.get("files")
    files = (
        [item for item in raw_files if isinstance(item, dict)]
        if isinstance(raw_files, list)
        else []
    )
    text = event.get("text") or ""
    context = {
        "channel": channel,
        "thread_ts": event.get("thread_ts") or timestamp,
        "conversation_thread": event.get("thread_ts") or "",
    }
    source = f"slack:{channel}:{timestamp}"
    parts: list[tuple[str, str, dict[str, object]]] = []
    if isinstance(text, str) and text:
        parts.append((source + (":text" if files else ""), text, {"msg_type": 1}))
    for index, item in enumerate(files):
        mime = item.get("mimetype") or "application/octet-stream"
        kind = 2 if mime.startswith("image/") else 4 if mime.startswith("audio/") else 3
        extra: dict[str, object] = {"msg_type": kind}
        if kind in (2, 4):
            extra.update(
                media_pending=True,
                incoming_media={
                    "url": item.get("url_private_download") or item.get("url_private"),
                    "mime": mime,
                    "name": item.get("name") or "attachment",
                },
            )
        parts.append(
            (
                f"{source}:file:{item.get('id') or index}",
                "" if kind in (2, 4) else "[客户发送了文件]",
                extra,
            )
        )
    async with sessions() as db:
        for message_id, content, extra in parts:
            await capture(
                db,
                NormalizedMessage(
                    source="slack",
                    from_uid=user,
                    content=content,
                    platform_api_key=platform.api_key,
                    platform_id=str(platform.id),
                    platform_type="slack",
                    extra={**extra, "message_id": message_id, "slack": context},
                ),
                commit=False,
            )
        await db.commit()
