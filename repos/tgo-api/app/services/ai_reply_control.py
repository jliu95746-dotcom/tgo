"""Own one complete reply, including factual and expression-only phases."""

import asyncio
import traceback
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from anyio import CancelScope
from pydantic import JsonValue

from app.core.logging import get_logger
from app.schemas.ai_runs import ReplyFailure, ReplyRun
from app.schemas.reply_phase import ReplyPhaseIdentity
from app.services.run_registry import (
    RegistryConflict,
    RegistryUnavailable,
    run_registry,
)

POLL_SECONDS = 0.25
PHASE_WAIT_SECONDS = 4.0
ReplyEvent = dict[str, JsonValue]
logger = get_logger(__name__)


class ReplyStopped(Exception):
    pass


@dataclass
class ReplyControl:
    identity: ReplyRun
    cancel_requested: bool = False
    control_failed: bool = False
    upstream_run_id: str | None = None
    upstream_open: bool = False
    phase: ReplyPhaseIdentity | None = None
    finishing: bool = False


_current: ContextVar[ReplyControl | None] = ContextVar("reply_control", default=None)


async def begin_ai_phase() -> ReplyPhaseIdentity | None:
    control = _current.get()
    if control is None:
        return None
    phase = ReplyPhaseIdentity(
        project_id=control.identity.project_id,
        client_msg_no=control.identity.client_msg_no,
        generation=control.identity.generation,
        phase_id=uuid4(),
        proof=uuid4(),
    )
    item = await run_registry.start_phase(control.identity, phase)
    if item is not None and item.status == "cancel_requested":
        control.cancel_requested = True
        raise ReplyStopped("本次回复已停止")
    if item is None or item.status != "active" or item.phase != phase:
        raise RegistryUnavailable("AI phase ownership unavailable")
    control.phase = phase
    control.upstream_open = True
    control.upstream_run_id = None
    return phase


async def finish_ai_phase(phase: ReplyPhaseIdentity | None) -> None:
    """A normal final response also proves the upstream model phase ended."""
    if phase is not None and await run_registry.end_phase(phase) is None:
        raise RegistryUnavailable("AI phase ownership expired")
    control = _current.get()
    if control is not None and control.phase == phase:
        control.upstream_open = False
        control.upstream_run_id = None


@asynccontextmanager
async def tracked_ai_request() -> AsyncIterator[ReplyPhaseIdentity | None]:
    phase = await begin_ai_phase()
    yield phase
    # Not a finally: disconnected requests require a private server receipt.
    await finish_ai_phase(phase)


async def wait_for_phase_end(control: ReplyControl) -> bool:
    if not control.upstream_open:
        return True
    if control.phase is None:
        return False
    try:
        async with asyncio.timeout(PHASE_WAIT_SECONDS):
            while True:
                item = await run_registry.get(
                    control.identity.project_id, control.identity.client_msg_no
                )
                if (
                    item is None
                    or item.generation != control.identity.generation
                    or item.phase != control.phase
                ):
                    return False
                if item.phase_ended:
                    return True
                await asyncio.sleep(0.05)
    except (TimeoutError, RegistryUnavailable):
        return False


async def begin_reply_publication() -> None:
    control = _current.get()
    if control is None:
        raise RegistryUnavailable("Reply has no publication owner")
    item = await run_registry.begin_publication(control.identity)
    if item is None:
        raise RegistryUnavailable("Reply publication ownership expired")
    if item.status == "cancel_requested":
        control.cancel_requested = True
        raise ReplyStopped("本次回复已停止")
    if item.status != "publishing":
        raise RegistryUnavailable("Reply cannot be published")


async def tracked_ai_stream(
    source_factory: Callable[
        [ReplyPhaseIdentity | None], AsyncGenerator[tuple[str, ReplyEvent], None]
    ],
) -> AsyncGenerator[tuple[str, ReplyEvent], None]:
    phase = await begin_ai_phase()
    control = _current.get()
    source = source_factory(phase)
    async with aclosing(source):
        async for event, data in source:
            event_type = data.get("event_type") or event
            if not isinstance(event_type, str):
                event_type = event
            terminal = event_type in ("workflow_completed", "workflow_failed")
            payload = data.get("data")
            if control is not None:
                if event_type == "agent_execution_started" and isinstance(
                    payload, dict
                ):
                    run_id = payload.get("execution_id")
                    if isinstance(run_id, str) and run_id:
                        control.upstream_run_id = run_id
            if terminal:
                # Callers may break immediately after receiving this event.
                await source.aclose()
                await finish_ai_phase(phase)
            yield event, data
            if terminal:
                return


async def controlled_reply(
    identity: ReplyRun,
    source_factory: Callable[[], AsyncGenerator[ReplyEvent, None]],
    publish_error: Callable[[str], Awaitable[None]],
    stop_upstream: Callable[[str], Awaitable[bool]],
) -> AsyncGenerator[ReplyEvent, None]:
    """Use an owned producer so stop never cancels the HTTP consumer task."""
    try:
        await run_registry.start(identity)
    except (RegistryUnavailable, RegistryConflict):
        message = "回复任务暂时无法启动，请稍后重试。"
        await publish_error(message)
        yield {"event_type": "workflow_failed", "data": {"error_message": message}}
        return

    control = ReplyControl(identity)
    queue: asyncio.Queue[ReplyEvent | None] = asyncio.Queue()
    producer_started = False

    async def produce() -> None:
        nonlocal producer_started
        producer_started = True
        token = _current.set(control)
        completed, failed = False, False
        failure_reason: ReplyFailure | None = None
        message = "回复任务已中断，请重试。"
        try:
            source = source_factory()
            async with aclosing(source):
                async for event in source:
                    completed |= event.get("event_type") in {"workflow_completed", "human_handoff"}
                    failed |= event.get("event_type") == "workflow_failed"
                    queue.put_nowait(event)
        except asyncio.CancelledError:
            failure_reason = (
                "control_unavailable" if control.control_failed else "generation_failed"
            )
        except Exception as exc:
            failure_reason = "generation_failed"
            frames = traceback.extract_tb(exc.__traceback__)
            locations = [
                f"{frame.filename.rsplit('/', 1)[-1]}:{frame.name}:{frame.lineno}"
                for frame in frames[-5:]
            ]
            logger.warning(
                "Reply producer failed: %s; locations=%s",
                type(exc).__name__,
                locations,
            )
        finally:
            control.finishing = True
            try:
                with CancelScope(shield=True):
                    if not completed and control.upstream_run_id:
                        try:
                            async with asyncio.timeout(1):
                                await stop_upstream(control.upstream_run_id)
                        except (Exception, asyncio.CancelledError):
                            # A cancelled HTTP stop request must not abort cleanup.
                            # Only the private phase receipt can confirm termination.
                            pass
                    # An accepted cancel signal is not proof of task cleanup.
                    upstream_confirmed = await wait_for_phase_end(control)
                    outcome: Literal["cancelled", "completed", "failed"] = "failed"
                    if control.cancel_requested and upstream_confirmed:
                        message = "本次回复已停止"
                        outcome = "cancelled"
                        failure_reason = None
                    elif control.cancel_requested:
                        message = "回复已拦截，但 AI 任务停止尚未确认，请检查任务状态。"
                        failure_reason = "upstream_stop_unconfirmed"
                    elif completed and not failed:
                        outcome = "completed"
                    elif control.control_failed:
                        message = "回复控制连接中断，已拦截本次回复，请重试。"
                    try:
                        saved = await run_registry.finish(
                            identity, outcome, failure_reason
                        )
                        if (
                            control.cancel_requested
                            and saved is not None
                            and saved.status == "cancelled"
                        ):
                            message = "本次回复已停止"
                    except RegistryUnavailable:
                        logger.warning("Reply final status could not be recorded")
                    if not completed and not failed:
                        try:
                            async with asyncio.timeout(5):
                                await publish_error(message)
                        except Exception as exc:
                            logger.warning(
                                "Reply stop notice failed: %s", type(exc).__name__
                            )
                        queue.put_nowait(
                            {
                                "event_type": "workflow_failed",
                                "data": {"error_message": message},
                            }
                        )
            finally:
                queue.put_nowait(None)
                _current.reset(token)

    async def watch(task: asyncio.Task[None]) -> None:
        try:
            while not task.done() and not control.finishing:
                item = await run_registry.heartbeat(identity)
                if item is None:
                    raise RegistryUnavailable("Reply ownership expired")
                if item.status == "cancel_requested":
                    control.cancel_requested = True
                    if not task.cancelling():
                        task.cancel()
                    return
                await asyncio.sleep(POLL_SECONDS)
        except Exception as exc:
            logger.warning("Reply control heartbeat failed: %s", type(exc).__name__)
            control.control_failed = True
            if not task.done() and not task.cancelling():
                task.cancel()

    producer = asyncio.create_task(produce())
    watcher = asyncio.create_task(watch(producer))
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
        await producer
    finally:
        with CancelScope(shield=True):
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if not producer.done() and not producer.cancelling():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            if not producer_started:
                try:
                    await run_registry.finish(identity, "failed", "generation_failed")
                except RegistryUnavailable:
                    pass
