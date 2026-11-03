"""Redact expired private shadow answers while retaining audit hashes."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.hidden_shadow_protocol import (
    HiddenShadowProtocol, HiddenShadowProtocolCase,
)
from app.models.hidden_shadow_gate_signoff import HiddenShadowGateSignoff


def redact_expired_hidden_shadow_runs(
    db: Session, *, now: datetime | None = None, days: int = 90,
    limit: int = 100, dry_run: bool = False,
) -> int:
    if days < 1 or limit < 1:
        raise ValueError("Retention days and batch limit must be positive")
    current = now or datetime.now(timezone.utc)
    unresolved_frozen_case = (
        select(HiddenShadowProtocolCase.candidate_id)
        .join(HiddenShadowProtocol)
        .outerjoin(
            HiddenShadowGateSignoff,
            and_(
                HiddenShadowGateSignoff.protocol_id
                == HiddenShadowProtocolCase.protocol_id,
                HiddenShadowGateSignoff.project_id
                == HiddenShadowProtocol.project_id,
                HiddenShadowGateSignoff.decided_at.is_not(None),
            ),
        )
        .where(
            HiddenShadowProtocolCase.candidate_id == HiddenShadowRun.id,
            HiddenShadowProtocolCase.selected_position.is_not(None),
            HiddenShadowProtocol.project_id == HiddenShadowRun.project_id,
            HiddenShadowGateSignoff.id.is_(None),
        )
        .correlate(HiddenShadowRun)
        .exists()
    )
    query = (
        select(HiddenShadowRun)
        .where(
            HiddenShadowRun.created_at < current - timedelta(days=days),
            HiddenShadowRun.answer.is_not(None),
            HiddenShadowRun.redacted_at.is_(None),
            ~unresolved_frozen_case,
        )
        .order_by(HiddenShadowRun.created_at, HiddenShadowRun.id)
        .limit(limit)
    )
    if not dry_run:
        query = query.with_for_update(skip_locked=True)
    rows = db.scalars(query).all()
    if dry_run:
        return len(rows)
    for row in rows:
        row.answer = None
        row.redacted_at = current
    db.commit()
    return len(rows)
