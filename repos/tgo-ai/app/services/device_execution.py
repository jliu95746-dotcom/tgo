"""Own a renewable device session for the full AI execution lifetime."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator, Callable
from uuid import UUID, uuid4

from anyio import CancelScope

from app.exceptions import ExternalServiceError
from app.models.internal import AgentExecutionContext
from app.schemas.device_session import (
    DeviceExecutionIdentity,
    DeviceFinishStatus,
)
from app.services.device_control_client import (
    DeviceControlClient,
    device_control_client,
)

HEARTBEAT_SECONDS = 15
logger = logging.getLogger(__name__)


@dataclass
class DeviceExecution:
    identity: DeviceExecutionIdentity
    status: DeviceFinishStatus = "failed"
    closed: bool = False
    heartbeat_failed: bool = False

    def complete(self, success: bool) -> None:
        self.status = "completed" if success else "failed"


_execution: ContextVar[DeviceExecution | None] = ContextVar(
    "device_execution", default=None
)


def current_device_execution() -> DeviceExecution | None:
    return _execution.get()


def device_headers_factory(
    project_id: str,
    device_id: str,
) -> Callable[[], dict[str, str]]:
    """Capture once: orphan tasks cannot shed the ended execution's fence."""
    execution = current_device_execution()
    project, device = UUID(project_id), UUID(device_id)
    if execution is not None and (
        execution.identity.project_id != project
        or execution.identity.device_id != device
    ):
        raise ExternalServiceError("device-control", "设备与执行记录不匹配")

    def headers() -> dict[str, str]:
        if execution is not None and execution.closed:
            raise ExternalServiceError("device-control", "设备任务已经结束，无法继续操作")
        return DeviceControlClient.headers(
            project,
            device,
            session_id=execution.identity.session_id if execution else None,
        )

    return headers


@asynccontextmanager
async def track_device_execution(
    context: AgentExecutionContext,
) -> AsyncIterator[DeviceExecution | None]:
    if (
        not context.agent.bound_device_id
        or context.disable_tools
        or context.response_purpose == "expression"
    ):
        # Nested expression-only runs must not inherit a device execution.
        token = _execution.set(None)
        try:
            yield None
        finally:
            _execution.reset(token)
        return

    identity = DeviceExecutionIdentity(
        project_id=UUID(context.project_id),
        device_id=UUID(context.agent.bound_device_id),
        session_id=uuid4(),
        agent_id=context.agent.id,
        agent_name=context.agent.name,
    )
    await device_control_client.start_session(identity)
    execution = DeviceExecution(identity)
    token = _execution.set(execution)
    owner = asyncio.current_task()
    assert owner is not None
    owned_cancellation = False

    async def heartbeat() -> None:
        nonlocal owned_cancellation
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                await device_control_client.heartbeat_session(identity)
        except Exception:
            execution.heartbeat_failed = True
            execution.closed = True
            # Do not steal an external disconnect/timeout cancellation.
            if not owner.cancelling():
                owned_cancellation = owner.cancel()

    keeper = asyncio.create_task(heartbeat())
    try:
        yield execution
        if execution.heartbeat_failed:
            raise ExternalServiceError("device-control", "执行记录连接中断，已停止后续操作")
    except asyncio.CancelledError:
        execution.status = "cancelled"
        if owned_cancellation and owner.cancelling() == 1:
            execution.status = "failed"
            raise ExternalServiceError(
                "device-control", "执行记录连接中断，已停止后续操作"
            ) from None
        raise
    except BaseException:
        execution.status = "failed"
        raise
    finally:
        execution.closed = True
        _execution.reset(token)
        if owned_cancellation:
            # Clear only our heartbeat's cancellation.
            owner.uncancel()
        # Bound cleanup also runs under ASGI's cancellation scope.
        with CancelScope(shield=True):
            keeper.cancel()
            with suppress(asyncio.CancelledError):
                await keeper
            try:
                async with asyncio.timeout(5):
                    await device_control_client.finish_session(
                        identity, execution.status
                    )
            except Exception as exc:
                # Lease expiry reports interruption, not success.
                logger.warning(
                    "Device session finish failed: %s", type(exc).__name__
                )
