"""Best-effort queue refresh events are emitted only after the owning commit."""

import asyncio
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models import VisitorWaitingQueue
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_PROJECT_STAFF
from app.utils.encoding import build_project_staff_channel_id

QueueChangeReason = Literal["entered", "updated"]
QueueChanges = dict[UUID, QueueChangeReason]
_KEY = "committed_queue_events"
_tasks: set[asyncio.Task[None]] = set()
logger = get_logger("services.queue_events")


def schedule_queue_update(
    db: Session, project_id: UUID, reason: QueueChangeReason = "updated"
) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        raise RuntimeError("Queue update requires a database transaction")
    pending = cast(dict[SessionTransaction, QueueChanges], db.info.setdefault(_KEY, {}))
    changes = pending.setdefault(transaction, {})
    if reason == "entered" or project_id not in changes:
        changes[project_id] = reason


async def publish_queue_updates(changes: QueueChanges) -> None:
    for project_id, reason in changes.items():
        try:
            with SessionLocal() as db:
                count = (
                    db.query(VisitorWaitingQueue)
                    .filter(
                        VisitorWaitingQueue.project_id == project_id,
                        VisitorWaitingQueue.status == "waiting",
                    )
                    .count()
                )
            await wukongim_client.send_queue_updated_event(
                channel_id=build_project_staff_channel_id(project_id),
                channel_type=CHANNEL_TYPE_PROJECT_STAFF,
                project_id=str(project_id),
                waiting_count=count,
                reason=reason,
            )
        except Exception as exc:
            logger.warning(
                "Committed queue refresh could not be published",
                extra={
                    "project_id": str(project_id),
                    "error_type": type(exc).__name__,
                },
            )


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    pending = cast(dict[SessionTransaction, QueueChanges], db.info.get(_KEY, {}))
    transaction = db.get_nested_transaction() or db.get_transaction()
    changes = pending.pop(transaction, {}) if transaction is not None else {}
    if not changes:
        return
    if transaction is not None and transaction.parent is not None:
        parent_changes = pending.setdefault(transaction.parent, {})
        for project_id, reason in changes.items():
            if reason == "entered" or project_id not in parent_changes:
                parent_changes[project_id] = reason
        return
    db.info.pop(_KEY, None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "Queue committed without a running event loop; refresh on reload"
        )
        return
    task = loop.create_task(publish_queue_updates(changes))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@event.listens_for(Session, "after_transaction_end")
def _after_rollback(db: Session, transaction: SessionTransaction) -> None:
    # after_commit has already consumed committed changes. This also handles
    # Session.close(), which does not dispatch after_soft_rollback.
    pending = cast(dict[SessionTransaction, QueueChanges], db.info.get(_KEY, {}))
    pending.pop(transaction, None)
    if transaction.parent is None:
        db.info.pop(_KEY, None)
