"""Read-only recovery of orphaned customer reply anchors in message history."""

import asyncio
import re
import time

from app.core.logging import get_logger
from app.schemas.ai_runs import ReplyRun
from app.schemas.wukongim import (
    WuKongIMChannelMessageSyncResponse,
    WuKongIMMessage,
)
from app.services.run_registry import RegistryUnavailable, TERMINAL, run_registry

logger = get_logger("services.reply_history")
OWNED_REPLY_ID = re.compile(r"ai_[0-9a-f]{32}")
# The normal active lease lasts 60s and is refreshed throughout generation.
# Leave room for an anchor/registry visibility race; age alone is never proof.
ORPHAN_GRACE_SECONDS = 120
LOOKUP_TIMEOUT_SECONDS = 3


def _incomplete_copy(message: WuKongIMMessage, run: ReplyRun | None) -> WuKongIMMessage:
    notice = "这条回复未正常完成，请重新发送消息。"
    if run is not None:
        if run.status == "cancelled":
            notice = "本次回复已停止"
        elif run.failure_reason == "upstream_stop_unconfirmed":
            notice = "回复已拦截，但 AI 任务停止尚未确认，请检查任务状态。"
        elif run.status == "completed":
            notice = "这条回复的发送结果未确认，请核实后再试。"
    result = message.model_copy(deep=True)
    result.end = 1
    result.error = notice
    if result.event_meta:
        result.event_meta.update(completed=True, open_event_count=0)
        for event in result.event_meta.get("events", []):
            if isinstance(event, dict) and event.get("status") == "open":
                event.update(status="error", error=notice)
    return result


async def reconcile_reply_history(
    response: WuKongIMChannelMessageSyncResponse,
    *,
    project_id: str,
    channel_id: str,
    channel_type: int,
) -> WuKongIMChannelMessageSyncResponse:
    """Annotate stale anchors, without deleting messages or writing IM events.

    Use only the authenticated project and requested channel. A missing lease
    means publication ownership expired, not that upstream cancellation was
    confirmed. Registry outages leave the original history untouched.
    """
    if channel_type != 251:
        return response
    now = time.time()
    candidates = [
        message
        for message in response.messages
        if message.channel_id == channel_id
        and message.channel_type == channel_type
        and message.from_uid.endswith("-staff")
        and OWNED_REPLY_ID.fullmatch(message.client_msg_no)
        and message.payload.get("type") == 100
        and 0 < message.timestamp <= now - ORPHAN_GRACE_SECONDS
        and message.end != 1
        and not message.error
        and not (message.event_meta or {}).get("completed")
    ]
    if not candidates:
        return response
    slots = asyncio.Semaphore(8)

    async def lookup(message: WuKongIMMessage) -> ReplyRun | None:
        async with slots:
            return await run_registry.get(project_id, message.client_msg_no)

    try:
        async with asyncio.timeout(LOOKUP_TIMEOUT_SECONDS):
            runs = await asyncio.gather(*(lookup(message) for message in candidates))
    except (RegistryUnavailable, TimeoutError):
        logger.warning("Reply history ownership check unavailable; kept original state")
        return response

    replacements: dict[int, WuKongIMMessage] = {}
    for message, run in zip(candidates, runs):
        if run is not None:
            if (
                run.project_id,
                run.client_msg_no,
                run.channel_id,
                run.channel_type,
            ) != (
                project_id,
                message.client_msg_no,
                channel_id,
                channel_type,
            ):
                continue
            if run.status not in TERMINAL:
                continue
        replacements[id(message)] = _incomplete_copy(message, run)
    return response.model_copy(
        update={
            "messages": [
                replacements.get(id(message), message) for message in response.messages
            ],
        }
    )
