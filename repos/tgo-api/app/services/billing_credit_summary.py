"""Current credits and the remaining balance approaching expiration."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.company_account import AICreditBatch


def credit_summary(
    db: Session, project_id: UUID, now: datetime
) -> tuple[int, int, int]:
    """Return granted, remaining and remaining within the next seven days."""
    total, remaining, expiring_soon = db.execute(
        select(
            func.coalesce(func.sum(AICreditBatch.amount), 0),
            func.coalesce(func.sum(AICreditBatch.remaining), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AICreditBatch.expires_at <= now + timedelta(days=7),
                            AICreditBatch.remaining,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(
            AICreditBatch.project_id == project_id,
            AICreditBatch.expires_at > now,
        )
    ).one()
    return total, remaining, expiring_soon
