"""Resume bounded external cleanup for visitors whose access is retired."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.ai_interaction_run import AIInteractionRun
from app.models.visitor import Visitor
from app.services.visitor_access_end import cleanup_retired_access
from app.services.wukongim_client import wukongim_client

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class EndRecoverySweep:
    checked: int = 0
    confirmed: int = 0
    pending: int = 0
    failed: int = 0
    next_cursor: UUID | None = None


def _candidate_ids(
    db: Session, *, after_visitor_id: UUID | None, limit: int,
) -> list[UUID]:
    pending_run = select(AIInteractionRun.id).where(
        AIInteractionRun.project_id == Visitor.project_id,
        AIInteractionRun.visitor_id == Visitor.id,
        AIInteractionRun.error_message == "visitor_access_ended",
        AIInteractionRun.stop_confirmed_at.is_(None),
    ).exists()
    pending = (
        or_(pending_run, Visitor.im_disconnect_requested_at.is_(None))
        if wukongim_client.enabled else pending_run
    )
    query = select(Visitor.id).where(
        Visitor.access_retired_at.is_not(None), pending,
    )
    if after_visitor_id is not None:
        query = query.where(Visitor.id > after_visitor_id)
    return list(db.scalars(query.order_by(Visitor.id).limit(limit)).all())


async def recover_pending_visitor_ends(
    session_factory: Callable[[], Session] = SessionLocal,
    *, after_visitor_id: UUID | None = None, limit: int = 5,
) -> EndRecoverySweep:
    """Visit a fair page and leave unknown work pending."""
    if limit < 1 or limit > 50:
        raise ValueError("Recovery limit must be between 1 and 50")
    with session_factory() as db:
        ids = _candidate_ids(
            db, after_visitor_id=after_visitor_id, limit=limit,
        )
        if not ids and after_visitor_id is not None:
            ids = _candidate_ids(db, after_visitor_id=None, limit=limit)

    confirmed = pending = failed = 0
    for visitor_id in ids:
        try:
            with session_factory() as db:
                visitor = db.get(Visitor, visitor_id)
                if visitor is None or visitor.access_retired_at is None:
                    continue
                outcome = await cleanup_retired_access(db, visitor)
                if (
                    outcome.upstream_stop_status == "confirmed"
                    and (
                        not wukongim_client.enabled
                        or outcome.im_disconnect_status == "requested"
                    )
                ):
                    confirmed += 1
                else:
                    pending += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            logger.warning(
                "Visitor end recovery failed: %s", type(exc).__name__,
            )
    return EndRecoverySweep(
        checked=len(ids), confirmed=confirmed,
        pending=pending, failed=failed,
        next_cursor=ids[-1] if ids else None,
    )


async def _run_periodically() -> None:
    cursor: UUID | None = None
    while True:
        try:
            result = await recover_pending_visitor_ends(
                after_visitor_id=cursor,
                limit=settings.VISITOR_END_RECOVERY_BATCH_SIZE,
            )
            cursor = result.next_cursor
            if result.checked or result.failed:
                logger.info(
                    "Visitor end recovery: checked=%s confirmed=%s "
                    "pending=%s failed=%s",
                    result.checked, result.confirmed,
                    result.pending, result.failed,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Visitor end recovery sweep failed: %s",
                type(exc).__name__,
            )
        await asyncio.sleep(settings.VISITOR_END_RECOVERY_INTERVAL_SECONDS)


def start_visitor_end_recovery() -> None:
    global _task
    if (
        settings.VISITOR_END_RECOVERY_ENABLED
        and (_task is None or _task.done())
    ):
        _task = asyncio.create_task(_run_periodically())


async def stop_visitor_end_recovery() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
