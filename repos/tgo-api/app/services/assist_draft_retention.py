"""Expire staff assist text while retaining review decisions."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.assist_draft_record import AssistDraftRecord
from app.models.shadow_gate_signoff import ShadowGateSignoff
from app.models.shadow_review_batch import (
    ShadowReviewBatch, ShadowReviewBatchItem,
)
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol, ShadowReviewProtocolCandidate,
)


def redact_expired_assist_drafts(
    db: Session, *, now: datetime | None = None, days: int = 90,
    limit: int = 100, dry_run: bool = False,
) -> int:
    """Redact one batch, or count it without changes when dry_run is set."""
    if days < 1 or limit < 1:
        raise ValueError("Retention days and batch limit must be positive")
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=days)

    active_protocol = (
        select(ShadowReviewProtocol.id)
        .where(
            ShadowReviewProtocol.project_id == AssistDraftRecord.project_id,
            ShadowReviewProtocol.finalized_at.is_(None),
            ShadowReviewProtocol.window_start <= AssistDraftRecord.created_at,
            ShadowReviewProtocol.window_end > AssistDraftRecord.created_at,
        )
        .correlate(AssistDraftRecord)
        .exists()
    )
    finished_signoff = and_(
        ShadowGateSignoff.protocol_id == ShadowReviewProtocol.id,
        ShadowGateSignoff.decided_at.is_not(None),
    )
    pending_candidate = (
        select(ShadowReviewProtocolCandidate.draft_id)
        .join(ShadowReviewProtocol)
        .outerjoin(ShadowGateSignoff, finished_signoff)
        .where(
            ShadowReviewProtocolCandidate.draft_id == AssistDraftRecord.id,
            ShadowReviewProtocol.project_id == AssistDraftRecord.project_id,
            ShadowGateSignoff.id.is_(None),
        )
        .correlate(AssistDraftRecord)
        .exists()
    )
    pending_batch = (
        select(ShadowReviewBatchItem.draft_id)
        .join(ShadowReviewBatch)
        .outerjoin(
            ShadowGateSignoff,
            and_(
                ShadowGateSignoff.batch_id == ShadowReviewBatch.id,
                ShadowGateSignoff.decided_at.is_not(None),
            ),
        )
        .where(
            ShadowReviewBatchItem.draft_id == AssistDraftRecord.id,
            ShadowReviewBatch.project_id == AssistDraftRecord.project_id,
            ShadowGateSignoff.id.is_(None),
        )
        .correlate(AssistDraftRecord)
        .exists()
    )
    query = (
        select(AssistDraftRecord)
        .where(
            AssistDraftRecord.created_at < cutoff,
            AssistDraftRecord.response_data["draft"].as_string() != "",
            ~active_protocol,
            ~pending_candidate,
            ~pending_batch,
        )
        .order_by(AssistDraftRecord.created_at, AssistDraftRecord.id)
        .limit(limit)
    )
    if not dry_run:
        query = query.with_for_update(skip_locked=True)
    rows = db.scalars(query).all()
    if dry_run:
        return len(rows)

    redacted = 0
    for row in rows:
        raw_draft = row.response_data.get("draft")
        if not isinstance(raw_draft, str):
            continue
        row.response_data = {
            "draft": "",
            "draft_sha256": sha256(raw_draft.encode("utf-8")).hexdigest(),
            "generation_trace": row.response_data.get("generation_trace", []),
            "retention_redacted_at": current.isoformat(),
        }
        redacted += 1
    db.commit()
    return redacted
