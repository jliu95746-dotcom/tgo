"""The queue cleanup worker's bounded, restart-safe application operation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import VisitorWaitingQueue
from app.services.queue_lifecycle import expire_entry
from app.services.queue_timeout_delivery import (
    deliver_timeout_notice,
    recover_timeout_notices,
)

logger = get_logger("services.queue_timeout")


async def process_queue_timeouts(
    db: Session, *, limit: int = 100, project_id: UUID | None = None
) -> int:
    query = db.query(VisitorWaitingQueue.id).filter(
        VisitorWaitingQueue.status == "waiting",
        VisitorWaitingQueue.expired_at <= datetime.utcnow(),
    )
    if project_id is not None:
        query = query.filter(VisitorWaitingQueue.project_id == project_id)
    ids = [
        row.id for row in query.order_by(VisitorWaitingQueue.expired_at).limit(limit)
    ]
    db.rollback()
    expired = 0
    for entry_id in ids:
        try:
            changed = expire_entry(db, entry_id)
            db.commit()
            if changed:
                expired += 1
                await deliver_timeout_notice(db, entry_id)
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Queue timeout processing failed",
                extra={
                    "entry_id": str(entry_id),
                    "error_type": type(exc).__name__,
                },
            )
    await recover_timeout_notices(db, limit=20, project_id=project_id)
    return expired
