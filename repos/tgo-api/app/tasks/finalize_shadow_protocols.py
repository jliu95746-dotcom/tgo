"Freeze official shadow samples shortly after their registered window \
ends."

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import TGOAPIException
from app.core.logging import get_logger
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.services.shadow_review_protocol_service import (
    ShadowReviewProtocolService,
)

logger = get_logger(__name__)
_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class FinalizationSweep:
    frozen: int = 0
    empty: int = 0
    skipped: int = 0
    failed: int = 0


def finalize_due_protocols(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    now: datetime | None = None,
) -> FinalizationSweep:
    (
        'Process each due project in its own transaction so failures stay '
        'local.'
    )
    cutoff = now or datetime.now(timezone.utc)
    with session_factory() as db:
        due_protocols: list[tuple[UUID, UUID]] = [
            (row.project_id, row.id)
            for row in db.execute(
                select(
                    ShadowReviewProtocol.project_id,
                    ShadowReviewProtocol.id,
                )
                .where(
                    ShadowReviewProtocol.finalized_at.is_(None),
                    ShadowReviewProtocol.window_end <= cutoff,
                )
                .order_by(
                    ShadowReviewProtocol.window_end, ShadowReviewProtocol.id
                )
            ).all()
        ]

    frozen = empty = skipped = failed = 0
    for project_id, protocol_id in due_protocols:
        try:
            with session_factory() as db:
                protocol = db.scalar(
                    select(ShadowReviewProtocol)
                    .where(
                        ShadowReviewProtocol.project_id == project_id,
                        ShadowReviewProtocol.id == protocol_id,
                    )
                    .with_for_update()
                )
                if protocol is None or protocol.finalized_at is not None:
                    skipped += 1
                    continue
                outcome = ShadowReviewProtocolService(db).finalize(
                    project_id, protocol_id
                )
            if outcome.status == "frozen":
                frozen += 1
            elif outcome.status == "empty":
                empty += 1
        except TGOAPIException as exc:
            failed += 1
            logger.warning(
                "Shadow protocol finalization rejected for project %s: %s",
                project_id,
                exc.code,
            )
        except Exception as exc:
            failed += 1
            logger.error(
                "Shadow protocol finalization failed for project %s: %s",
                project_id,
                type(exc).__name__,
            )
    return FinalizationSweep(
        frozen=frozen, empty=empty, skipped=skipped, failed=failed
    )


async def _run_periodically() -> None:
    while True:
        try:
            result = await asyncio.to_thread(finalize_due_protocols)
            if (
                result.frozen
                or result.empty
                or result.skipped
                or result.failed
            ):
                logger.info(
                    "Shadow protocol finalization sweep: frozen=%s empty=%s "
                    "skipped=%s failed=%s",
                    result.frozen,
                    result.empty,
                    result.skipped,
                    result.failed,
                )
        except Exception as exc:
            logger.error(
                "Shadow protocol finalization sweep failed: %s",
                type(exc).__name__,
            )
        await asyncio.sleep(settings.SHADOW_PROTOCOL_FINALIZE_INTERVAL_SECONDS)


def start_shadow_protocol_finalization() -> None:
    global _task
    if not settings.SHADOW_PROTOCOL_AUTO_FINALIZE_ENABLED:
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_run_periodically())


async def stop_shadow_protocol_finalization() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
