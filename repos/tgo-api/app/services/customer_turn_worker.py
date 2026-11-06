"""Recoverable turn execution, independent of browser and callback lifetimes."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from sqlalchemy import select, or_, and_

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.models.customer_turn import CustomerTurn
from app.schemas.chat import ChatCompletionRequest
from app.services.customer_turn_context import TurnExecution, current_execution
from app.services.customer_turn_scheduler import TurnScheduler
from app.services.customer_turn_store import TurnStore, utc

logger = logging.getLogger(__name__)
scheduler = TurnScheduler()
_runner: asyncio.Task[None] | None = None
_jobs: set[asyncio.Task[None]] = set()
_original_jobs: set[asyncio.Task[None]] = set()
_history_jobs: set[asyncio.Task[None]] = set()


def recover() -> list[tuple[UUID, float]]:
    now = datetime.now(timezone.utc)
    due = []
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(CustomerTurn)
                .where(
                    or_(
                        CustomerTurn.state == "collecting",
                        and_(
                            CustomerTurn.state == "processing",
                            CustomerTurn.lease_until < now,
                        ),
                        and_(
                            CustomerTurn.state == "ready",
                            CustomerTurn.ready_at
                            < now - timedelta(seconds=900),
                        ),
                        and_(
                            CustomerTurn.state == "sending",
                            CustomerTurn.updated_at
                            < now - timedelta(seconds=120),
                        ),
                    )
                )
                .order_by(CustomerTurn.due_at)
                .with_for_update(skip_locked=True)
                .limit(256)
            )
        )
        for row in rows:
            if (
                row.state == "sending"
                and (now - utc(row.updated_at)).total_seconds() > 120
            ):
                row.state = "uncertain"
                row.failure = "发送结果待核对，停止自动重试"
            elif (
                row.state == "ready"
                and (now - utc(row.ready_at or row.updated_at)).total_seconds()
                > 900
            ):
                TurnStore(db).release_permit(row)
                row.state, row.failure = "failed", "回复等待过久，请结合最新消息重新处理"
            elif (
                row.state == "processing"
                and row.lease_until
                and utc(row.lease_until) < now
            ):
                TurnStore(db).release_permit(row)
                # Old workers retain their version and fail the final fence.

                row.version += 1
                row.client_msg_no = "ai_" + uuid4().hex
                row.state, row.due_at, row.lease_owner = (
                    "collecting",
                    now,
                    None,
                )
            if row.state == "collecting":
                due.append((row.id, utc(row.due_at).timestamp()))
        db.commit()
    return due


def begin(
    turn_id: UUID, owner: UUID
) -> tuple[TurnExecution, ChatCompletionRequest] | None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        store = TurnStore(db)
        turn = store.get(turn_id, lock=True)
        if turn.state != "collecting" or utc(turn.due_at) > now:
            return None
        parts = store.parts(turn.id)
        media_failure = ""
        pending_parts = [part for part in parts if part.media_pending]
        if pending_parts:
            if all(
                (now - utc(part.created_at)).total_seconds() > 120
                for part in pending_parts
            ):
                media_failure = "图片或语音未接收完整，请补充文字或重新发送。"
            else:
                turn.due_at = now + timedelta(seconds=0.5)
                db.commit()
                return None
        if not turn.can_reply:
            turn.state = "recorded"
            turn.failure = media_failure or None
            db.commit()
            return None
        platform = db.get(Platform, turn.platform_id)
        if platform is None or not platform.is_active:
            turn.state = "cancelled"
            db.commit()
            return None
        payload = dict(parts[-1].payload)
        message = "\n".join(
            str(part.payload.get("message", ""))
            for part in parts
            if part.payload.get("msg_type") == 1
        )
        payload.update(
            api_key=platform.api_key,
            message=message or "客户发送了图片或语音",
            msg_type=1,
            media_file_id=None,
            media_pending=False,
            media_error=None,
            channel_id=turn.channel_id,
            channel_type=251,
            source_message_id=f"turn:{turn.id}:{turn.version}",
            wukongim_only=False,
            stream=False,
            forward_user_message_to_wukongim=False,
        )
        turn.state, turn.lease_owner = "processing", owner
        logger.info(
            "Customer turn phase turn=%s version=%s phase=collection seconds=%.3f parts=%s",
            turn.id,
            turn.version,
            (now - utc(turn.collect_started_at)).total_seconds(),
            len(parts),
        )
        if media_failure:
            turn.failure = media_failure
        turn.lease_until = now + timedelta(seconds=30)
        # A lease expiry cannot replay the old AI identity or its publication.

        db.commit()
        context = TurnExecution(
            turn.id,
            turn.version,
            turn.project_id,
            turn.platform_id,
            turn.visitor_id,
            turn.external_delivery,
            owner=owner,
            expired_media=bool(media_failure),
            client_msg_no=turn.client_msg_no,
        )
        extra = parts[-1].payload.get("extra") or {}
        extra = extra if isinstance(extra, dict) else {}
        context.isolated_history = bool(
            extra.get("wechat_local_group")
            or extra.get("reception_conversation")
        )
        return context, ChatCompletionRequest.model_validate(payload)


async def renew(context: TurnExecution, owner: UUID) -> None:
    while True:
        await asyncio.sleep(5)
        if not await scheduler.renew(context.turn_id, str(owner)):
            raise RuntimeError("Customer turn ownership expired")

        def heartbeat() -> bool:
            with SessionLocal() as db:
                turn = TurnStore(db).get(context.turn_id, lock=True)
                if (
                    turn.version != context.version
                    or turn.lease_owner != owner
                ):
                    return False
                turn.lease_until = datetime.now(timezone.utc) + timedelta(
                    seconds=30
                )
                db.commit()
                return True

        if not await asyncio.to_thread(heartbeat):
            raise RuntimeError("Customer turn version changed")


async def execute(turn_id: UUID, owner: UUID) -> None:
    started = time.monotonic()
    heartbeat = None
    invocation = None
    context = None
    try:
        prepared = await asyncio.to_thread(begin, turn_id, owner)
        if prepared is None:
            return
        context, request = prepared
        heartbeat = asyncio.create_task(renew(context, owner))
        token = current_execution.set(context)
        try:
            from app.api.v1.endpoints.chat import chat_completion

            # Use the same scoped visitor proof as the channel; never skip auth.

            from app.services.im_access import issue_visitor_im_token

            with SessionLocal() as db:
                visitor = db.get(Visitor, context.visitor_id)
                if visitor is None:
                    raise RuntimeError("Customer missing")
                proof = HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=issue_visitor_im_token(
                        visitor, timedelta(minutes=5)
                    ),
                )

            async def invoke() -> None:
                with SessionLocal() as db:
                    http_request = Request(
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/v1/chat/completion",
                            "headers": [],
                            "query_string": b"",
                        }
                    )
                    await chat_completion(
                        req=request,
                        request=http_request,
                        db=db,
                        credentials=proof,
                        x_saas_service_token=(
                            settings.SAAS_INTERNAL_TOKEN.get_secret_value()
                            if settings.SAAS_INTERNAL_TOKEN is not None
                            else None
                        ),
                    )

            invocation = asyncio.create_task(invoke())
            done, _ = await asyncio.wait(
                [invocation, heartbeat], return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                invocation.cancel()
                await asyncio.gather(invocation, return_exceptions=True)
                raise RuntimeError("Customer turn lease lost")
            await invocation
            if (
                context.media_failure
                and context.sender_uid
                and not context.staged
            ):
                from app.services.customer_turn_context import ensure_current

                await ensure_current()
                from app.services.chat_service import (
                    forward_ai_event_to_wukongim,
                )

                await forward_ai_event_to_wukongim(
                    event_type="agent_execution_started",
                    event_data={"data": {}},
                    channel_id=request.channel_id
                    or f"{context.visitor_id}-vtr",
                    channel_type=251,
                    client_msg_no=context.client_msg_no,
                    from_uid=context.sender_uid,
                )
                with SessionLocal() as db:
                    store = TurnStore(db)
                    row = store.stage(
                        turn_id,
                        context.version,
                        context.media_failure,
                        context.sender_uid,
                        None,
                        owner=owner,
                    )
                    row.failure = "媒体识别失败；已提示重发或补充文字"
                    if not context.external:
                        store.claim(turn_id, context.version)
                    db.commit()
                context.staged = True
                if not context.external:
                    from app.services.chat_service import (
                        forward_ai_event_to_wukongim,
                    )
                    from app.services.customer_turn_context import confirm_im

                    await forward_ai_event_to_wukongim(
                        event_type="workflow_completed",
                        event_data={
                            "data": {
                                "success": True,
                                "final_content": context.media_failure,
                                "total_chunks": 0,
                            }
                        },
                        channel_id=request.channel_id
                        or f"{context.visitor_id}-vtr",
                        channel_type=251,
                        client_msg_no=row.client_msg_no,
                        from_uid=context.sender_uid,
                    )
                    await confirm_im()
        finally:
            current_execution.reset(token)
        if not context.staged:

            def failed() -> None:
                with SessionLocal() as db:
                    row = TurnStore(db).get(turn_id, lock=True)
                    if (
                        row.version == context.version
                        and row.state == "processing"
                    ):
                        row.state = "failed"
                        row.failure = "本轮未生成可发送答复，请人工核对"
                        db.commit()

            await asyncio.to_thread(failed)
        logger.info(
            "Customer turn completed turn=%s version=%s seconds=%.3f",
            turn_id,
            context.version,
            time.monotonic() - started,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "Customer turn execution failed turn=%s error_type=%s",
            turn_id,
            type(error).__name__,
        )
        if context is not None:
            with SessionLocal() as db:
                row = TurnStore(db).get(turn_id, lock=True)
                if (
                    row.version == context.version
                    and row.state == "processing"
                ):
                    row.state, row.failure = "failed", "回复未完成，请人工核对"
                    db.commit()
    finally:
        if invocation and not invocation.done():
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await scheduler.release(turn_id, str(owner))
        with SessionLocal() as db:
            row = TurnStore(db).get(turn_id)
            due = (
                utc(row.due_at).timestamp()
                if row.state == "collecting"
                else None
            )
        if due is not None:
            await scheduler.enqueue(turn_id, due)
        else:
            await scheduler.remove_if_due(turn_id, utc(row.due_at).timestamp())


async def run() -> None:
    next_recovery = 0.0
    while True:
        try:
            if time.monotonic() >= next_recovery:
                for turn_id, due in await asyncio.to_thread(recover):
                    await scheduler.enqueue(turn_id, due)
                next_recovery = time.monotonic() + 5
            from app.services.customer_turn_delivery import due_turns, deliver
            from app.services.customer_turn_history import (
                pending,
                publish as publish_history,
            )

            for turn_id in await asyncio.to_thread(pending):
                if len(_history_jobs) >= 8:
                    break
                identity, owner = (
                    uuid5(NAMESPACE_URL, "turn-history:" + str(turn_id)),
                    uuid4(),
                )
                if await scheduler.acquire(identity, str(owner)):

                    async def history(
                        key: UUID = turn_id,
                        lock: UUID = identity,
                        lease: UUID = owner,
                    ) -> None:
                        try:
                            await publish_history(key)
                        finally:
                            await scheduler.release(lock, str(lease))

                    task = asyncio.create_task(history())
                    _history_jobs.add(task)
                    task.add_done_callback(_history_jobs.discard)
            from app.services.customer_turn_originals import (
                pending_actors,
                forward,
            )

            for actor in await asyncio.to_thread(pending_actors):
                if len(_original_jobs) >= 8:
                    break
                identity, owner = (
                    uuid5(NAMESPACE_URL, "turn-originals:" + actor),
                    uuid4(),
                )
                if await scheduler.acquire(identity, str(owner)):

                    async def originals(
                        key: str = actor,
                        lock: UUID = identity,
                        lease: UUID = owner,
                    ) -> None:
                        try:
                            await forward(key)
                        except Exception as error:
                            logger.warning(
                                "Original customer message waiting: %s",
                                type(error).__name__,
                            )
                        finally:
                            await scheduler.release(lock, str(lease))

                    task = asyncio.create_task(originals())
                    _original_jobs.add(task)
                    task.add_done_callback(_original_jobs.discard)
            for turn_id in await asyncio.to_thread(due_turns):
                if len(_jobs) >= 8:
                    break
                owner = uuid4()
                if await scheduler.acquire(turn_id, str(owner)):

                    async def publish(
                        identity: UUID = turn_id, lease: UUID = owner
                    ) -> None:
                        try:
                            await deliver(identity)
                        finally:
                            await scheduler.release(identity, str(lease))

                    task = asyncio.create_task(publish())
                    _jobs.add(task)
                    task.add_done_callback(_jobs.discard)
            for turn_id in await scheduler.due():
                if len(_jobs) >= 8:
                    break
                owner = uuid4()
                if await scheduler.acquire(turn_id, str(owner)):
                    task = asyncio.create_task(execute(turn_id, owner))
                    _jobs.add(task)
                    task.add_done_callback(_jobs.discard)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Customer turn scheduler unavailable: %s", type(error).__name__
            )
        await asyncio.sleep(0.2)


def start() -> None:
    global _runner
    if settings.CUSTOMER_TURNS_ENABLED and _runner is None:
        _runner = asyncio.create_task(run())


async def stop() -> None:
    global _runner
    if _runner:
        _runner.cancel()
        await asyncio.gather(_runner, return_exceptions=True)
        _runner = None
    for job in [*list(_jobs), *list(_original_jobs), *list(_history_jobs)]:
        job.cancel()
    await asyncio.gather(
        *list(_jobs),
        *list(_original_jobs),
        *list(_history_jobs),
        return_exceptions=True,
    )
    await scheduler.close()
