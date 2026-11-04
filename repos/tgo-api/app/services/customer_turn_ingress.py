"""Keep HTTP/SSE compatible while one durable worker owns each customer turn."""

import asyncio
from collections.abc import AsyncIterator
import json
import logging
import re
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.models.customer_turn import CustomerTurn
from app.schemas.chat import ChatCompletionRequest
from app.schemas.customer_turn import TurnReceipt
from app.services.customer_turn_store import OPEN_STATES, TurnStore, utc

logger = logging.getLogger(__name__)
HUMAN_REQUEST = re.compile(r"^\s*(?:请|帮我|我要|给我|麻烦)?\s*(?:转人工|找人工|人工客服|人工接待)[。！!\s]*$")


async def stop_run(project: UUID, client_msg_no: str | None) -> None:
    if not client_msg_no:
        return
    from app.services.ai_reply_control import run_registry

    try:
        run = await run_registry.get(str(project), client_msg_no)
        if run and run.status == "active":
            await run_registry.request_cancel(run)
    except Exception as error:
        # The durable version fence still prevents any old publication.
        logger.warning("Turn stop signal deferred: %s", type(error).__name__)


async def cancel_customer_turns(db: Session, visitor: Visitor) -> None:
    rows = list(
        db.scalars(
            select(CustomerTurn)
            .where(
                CustomerTurn.visitor_id == visitor.id,
                CustomerTurn.project_id == visitor.project_id,
                CustomerTurn.state.in_(OPEN_STATES),
            )
            .with_for_update()
        )
    )
    stopped = []
    for row in rows:
        TurnStore(db).release_permit(row)
        row.state = "cancelled"
        stopped.append(row.client_msg_no)
    db.commit()
    from app.services.customer_turn_context import current_execution

    execution = current_execution.get()
    for cid in stopped:
        if execution is not None and cid == execution.client_msg_no:
            continue  # Finish the human assignment that this same run requested.
        await stop_run(visitor.project_id, cid)


async def ingest(
    db: Session,
    platform: Platform,
    visitor: Visitor,
    request: ChatCompletionRequest,
    *,
    can_reply: bool,
) -> TurnReceipt:
    extra = request.extra or {}
    sender = str(
        extra.get("wechat_local_author") or extra.get("reception_author") or visitor.id
    )
    conversation = extra.get("reception_conversation")
    if not isinstance(conversation, str) or not re.fullmatch(
        r"[a-f0-9]{64}", conversation
    ):
        conversation = None
    channel = request.channel_id or f"{visitor.id}-vtr"
    from app.utils.encoding import build_visitor_channel_id

    if channel != build_visitor_channel_id(visitor.id) or request.channel_type not in (
        None,
        251,
    ):
        raise HTTPException(403, "客户消息只能进入其所属会话")
    external = platform.type != "website"
    admitted, previous_run = TurnStore(db).ingest(
        project=platform.project_id,
        platform=platform.id,
        visitor=visitor.id,
        channel=channel,
        sender=sender,
        request=request,
        can_reply=can_reply,
        external=external,
        conversation=conversation,
    )
    db.commit()
    await stop_run(platform.project_id, previous_run)
    from app.services.customer_turn_worker import scheduler

    due = utc(TurnStore(db).get(admitted.turn_id).due_at).timestamp()
    try:
        await scheduler.enqueue(admitted.turn_id, due)
    except Exception as error:
        logger.warning(
            "Turn persisted; wakeup requires recovery: %s", type(error).__name__
        )
    return admitted


def snapshot(admitted: TurnReceipt) -> dict[str, object]:
    with SessionLocal() as db:
        turn = TurnStore(db).get(admitted.turn_id)
        if turn.version != admitted.version:
            return {"event_type": "superseded", "success": True}
        if (
            turn.state == "sent" or turn.external_delivery and turn.state == "ready"
        ) and turn.reply_text:
            return {
                "event_type": "workflow_completed",
                "success": True,
                "message": turn.reply_text,
                "turn_id": str(turn.id),
                "version": turn.version,
                "client_msg_no": turn.client_msg_no,
                "channel_id": turn.channel_id,
                "channel_type": turn.channel_type,
                "delivery_required": turn.external_delivery,
                "visitor_id": str(turn.visitor_id),
            }
        if turn.state in {"failed", "cancelled", "uncertain", "recorded"}:
            return {
                "event_type": "human_handoff"
                if turn.state == "uncertain"
                else "superseded",
                "success": True,
                "message": turn.failure or "本轮已停止自动回复",
            }
        return {}


async def response(
    request: ChatCompletionRequest,
    admitted: TurnReceipt,
    visitor: Visitor,
    *,
    can_reply: bool,
) -> StreamingResponse | dict[str, object]:
    accepted = {
        "success": True,
        "event_type": "accepted" if can_reply else "received",
        "visitor_id": str(visitor.id),
        **admitted.model_dump(mode="json"),
    }
    if request.wukongim_only or not can_reply:
        if request.stream is False:
            return accepted

        async def accepted_events() -> AsyncIterator[str]:
            yield "event: accepted\ndata: " + json.dumps(accepted) + "\n\n"

        return StreamingResponse(accepted_events(), media_type="text/event-stream")

    async def wait() -> dict[str, object]:
        deadline = asyncio.get_running_loop().time() + 300
        while asyncio.get_running_loop().time() < deadline:
            value = await asyncio.to_thread(snapshot, admitted)
            if value:
                return value
            await asyncio.sleep(0.1)
        raise HTTPException(504, "本轮仍在处理中，请查看会话状态")

    if request.stream is False:
        return await wait()

    async def events() -> AsyncIterator[str]:
        deadline = asyncio.get_running_loop().time() + 300
        last_keepalive = 0.0
        while asyncio.get_running_loop().time() < deadline:
            result = await asyncio.to_thread(snapshot, admitted)
            if result:
                if result["event_type"] == "workflow_completed":
                    final = {
                        "success": True,
                        "final_content": result["message"],
                        **{
                            k: result[k]
                            for k in (
                                "turn_id",
                                "version",
                                "client_msg_no",
                                "channel_id",
                                "channel_type",
                                "delivery_required",
                            )
                        },
                    }
                    chunk = {
                        "event_type": "agent_content_chunk",
                        "data": {"content_chunk": result["message"]},
                    }
                    yield "event: agent_content_chunk\ndata: " + json.dumps(
                        chunk, ensure_ascii=False
                    ) + "\n\n"
                    for kind in ("agent_response_complete", "workflow_completed"):
                        payload = {"event_type": kind, "data": {"data": final}}
                        yield f"event: {kind}\ndata: " + json.dumps(
                            payload, ensure_ascii=False
                        ) + "\n\n"
                else:
                    yield "event: superseded\ndata: " + json.dumps(
                        result, ensure_ascii=False
                    ) + "\n\n"
                return
            if asyncio.get_running_loop().time() - last_keepalive >= 10:
                yield ": waiting for customer turn\n\n"
                last_keepalive = asyncio.get_running_loop().time()
            await asyncio.sleep(0.1)
        yield 'event: workflow_failed\ndata: {"event_type":"workflow_failed","data":{"error_message":"回复处理超时"}}\n\n'

    return StreamingResponse(events(), media_type="text/event-stream")
