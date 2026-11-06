"""Bounded, leased and fair authority checks for waiting source events."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.execution import WorkflowExecution
from app.services.event_trigger import (
    EventSourceStopped,
    EventSourceUnavailable,
    require_active_internal_event,
)
from app.services.execution_lifecycle import ExecutionStopped
from app.services.webhook_runtime import (
    WebhookSourceStopped,
    WebhookSourceUnavailable,
    require_active_webhook,
)
from app.services.timer_probe import probe_scope

BATCH_SIZE = 100
BUDGET_SECONDS = 45.0
CHECK_TIMEOUT_SECONDS = 10.0
LEASE_SECONDS = 90
CURSOR_SECONDS = 7 * 24 * 60 * 60
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

ADVANCE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""
RELEASE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


class AuditProgress(Protocol):
    async def acquire(self, owner: str) -> bool:
        ...

    async def cursor(self) -> str | None:
        ...

    async def advance(self, owner: str, cursor: str) -> bool:
        ...

    async def release(self, owner: str) -> None:
        ...


class RedisAuditProgress:
    def __init__(self, broker: Redis, namespace: str):
        self.broker = broker
        self.lease_key = namespace + ":lease"
        self.cursor_key = namespace + ":cursor"

    async def acquire(self, owner: str) -> bool:
        return bool(
            await self.broker.set(
                self.lease_key,
                owner,
                nx=True,
                ex=LEASE_SECONDS,
            )
        )

    async def cursor(self) -> str | None:
        value = await self.broker.get(self.cursor_key)
        if value is None or value in (b"", ""):
            return None
        try:
            decoded = (
                value.decode("ascii") if isinstance(value, bytes) else value
            )
            if not isinstance(decoded, str) or len(decoded) != 36:
                raise ValueError()
            return str(UUID(decoded))
        except (ValueError, UnicodeError):
            raise RuntimeError("事件来源巡检进度无效。") from None

    async def advance(self, owner: str, cursor: str) -> bool:
        return bool(
            await self.broker.execute_command(
                "EVAL",
                ADVANCE,
                2,
                self.lease_key,
                self.cursor_key,
                owner,
                cursor,
                str(CURSOR_SECONDS),
            )
        )

    async def release(self, owner: str) -> None:
        await self.broker.execute_command(
            "EVAL", RELEASE, 1, self.lease_key, owner
        )


@dataclass(frozen=True)
class AuditCandidate:
    execution_id: str
    project_id: str
    workflow_id: str
    status: Literal["waiting_approval", "pending_resume"]
    source_channel: Literal["internal_event", "external_webhook"]


async def candidates(
    sessions: SessionFactory,
    after: str | None,
    limit: int,
) -> list[AuditCandidate]:
    statement = (
        select(
            WorkflowExecution.id,
            WorkflowExecution.project_id,
            WorkflowExecution.workflow_id,
            WorkflowExecution.status,
            WorkflowExecution.source_channel,
        )
        .where(
            or_(
                and_(
                    WorkflowExecution.source_channel == "internal_event",
                    WorkflowExecution.visitor_session_id.is_not(None),
                ),
                WorkflowExecution.source_channel == "external_webhook",
            ),
            WorkflowExecution.execution_mode == "production",
            WorkflowExecution.status.in_(
                ("waiting_approval", "pending_resume")
            ),
        )
        .order_by(WorkflowExecution.id)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(WorkflowExecution.id > after)
    async with sessions() as db:
        rows = (await asyncio.wait_for(db.execute(statement), timeout=5)).all()
        return [
            AuditCandidate(
                row.id,
                row.project_id,
                row.workflow_id,
                "waiting_approval"
                if row.status == "waiting_approval"
                else "pending_resume",
                "internal_event"
                if row.source_channel == "internal_event"
                else "external_webhook",
            )
            for row in rows
        ]


async def audit_waiting_event_sources(
    sessions: SessionFactory,
    progress: AuditProgress,
    *,
    limit: int = BATCH_SIZE,
    clock: Callable[[], float] = monotonic,
) -> int:
    """Only lease holders advance the cursor, including after restart."""
    if type(limit) is not int or not 1 <= limit <= BATCH_SIZE:
        raise ValueError("事件来源巡检批量大小无效。")
    owner = uuid4().hex
    if not await progress.acquire(owner):
        return 0
    start = clock()
    stopped = 0
    try:
        cursor = await progress.cursor()
        rows = await candidates(sessions, cursor, limit)
        if not rows and cursor is not None:
            rows = await candidates(sessions, None, limit)
        if not rows:
            await progress.advance(owner, "")
            return 0
        for row in rows:
            if clock() - start >= BUDGET_SECONDS:
                break
            async with sessions() as db:
                try:
                    guard = (
                        require_active_internal_event
                        if row.source_channel == "internal_event"
                        else require_active_webhook
                    )
                    await asyncio.wait_for(
                        guard(
                            db,
                            row.project_id,
                            row.workflow_id,
                            row.execution_id,
                            expected_status=row.status,
                            defer_unavailable=True,
                        ),
                        timeout=CHECK_TIMEOUT_SECONDS,
                    )
                except (EventSourceStopped, WebhookSourceStopped) as error:
                    stopped += int(error.changed)
                except (
                    EventSourceUnavailable,
                    WebhookSourceUnavailable,
                    asyncio.TimeoutError,
                ):
                    await db.rollback()
                except ExecutionStopped:
                    await db.rollback()  # A concurrent state change won.
            if not await progress.advance(owner, row.execution_id):
                break  # An expired owner must never overwrite a new cursor.
        return stopped
    finally:
        await progress.release(owner)


def audit_namespace() -> str:
    scope = probe_scope()
    if scope is None:
        raise RuntimeError("事件来源巡检内部核验未配置。")
    return "tgo:workflow:event-source-audit:v1:" + scope


async def sweep_waiting_event_sources() -> int:
    from celery_app.task_database import workflow_task_session

    async with Redis.from_url(
        settings.REDIS_URL,
        socket_timeout=5,
        socket_connect_timeout=2,
    ) as broker:
        return await audit_waiting_event_sources(
            workflow_task_session,
            RedisAuditProgress(broker, audit_namespace()),
        )
