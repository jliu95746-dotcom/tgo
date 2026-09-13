"""Wait for the reply owner to confirm a stop; queued is not completed."""

import asyncio

from fastapi import HTTPException

from app.schemas.ai_runs import ReplyCancelResponse, ReplyRun
from app.services import ai_reply_control as control
from app.services.run_registry import RegistryUnavailable

CANCEL_WAIT_SECONDS = 6.0


async def get_reply_run(project_id: str, client_msg_no: str) -> ReplyRun:
    try:
        item = await control.run_registry.get(project_id, client_msg_no)
    except RegistryUnavailable as exc:
        raise HTTPException(503, "停止服务暂时不可用，请稍后重试。") from exc
    if item is None:
        raise HTTPException(404, "没有找到可停止的回复，请刷新后确认。")
    return item


async def cancel_reply(item: ReplyRun) -> ReplyCancelResponse:
    try:
        async with asyncio.timeout(CANCEL_WAIT_SECONDS):
            current = await control.run_registry.request_cancel(item)
            while True:
                if current is None or current.generation != item.generation:
                    raise HTTPException(404, "回复任务已变化，请刷新后确认。")
                if current.status == "cancelled":
                    return ReplyCancelResponse(client_msg_no=item.client_msg_no)
                if current.status in {"publishing", "completed"}:
                    raise HTTPException(409, "回复已开始发送，无法通过停止按钮撤回。")
                if current.status == "failed":
                    if current.failure_reason == "upstream_stop_unconfirmed":
                        raise HTTPException(503, "回复已拦截，但 AI 任务停止尚未确认。")
                    raise HTTPException(409, "回复任务已中断，请刷新后确认。")
                await asyncio.sleep(0.05)
                current = await control.run_registry.get(
                    item.project_id, item.client_msg_no
                )
    except TimeoutError as exc:
        raise HTTPException(504, "停止请求已提交，尚未收到确认，请稍后重试。") from exc
    except RegistryUnavailable as exc:
        raise HTTPException(503, "停止服务暂时不可用，请稍后重试。") from exc
