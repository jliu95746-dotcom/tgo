"""Find independent shadow answers paired with confirmed human replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Generator
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.assist_draft_record import AssistDraftRecord
from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.platform import Platform
from app.models.staff_message_delivery import StaffMessageDelivery
from app.services.hidden_shadow_run import GENERATION_VERSION
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE


@dataclass(frozen=True)
class HiddenShadowComparison:
    candidate_id: UUID
    visitor_id: UUID
    staff_delivery_id: UUID
    source_message_id: str
    source_content_sha256: str
    candidate_sha256: str
    staff_reply_sha256: str
    candidate_completed_at: datetime
    staff_reply_created_at: datetime


def independent_shadow_comparisons(
    db: Session,
    project_id: UUID,
    *,
    window_start: datetime,
    window_end: datetime,
) -> Generator[HiddenShadowComparison, None, None]:
    """Yield only fully traced cases that never had a visible assist draft."""
    visible_draft = (
        select(AssistDraftRecord.id)
        .where(
            AssistDraftRecord.project_id == HiddenShadowRun.project_id,
            AssistDraftRecord.visitor_id == HiddenShadowRun.visitor_id,
            AssistDraftRecord.source_message_id
            == HiddenShadowRun.source_message_id,
            AssistDraftRecord.created_at
            <= StaffMessageDelivery.created_at,
        )
        .correlate(HiddenShadowRun, StaffMessageDelivery)
        .exists()
    )
    ranked = (
        select(
            HiddenShadowRun.id.label("candidate_id"),
            StaffMessageDelivery.id.label("delivery_id"),
            func.row_number().over(
                partition_by=HiddenShadowRun.id,
                order_by=(
                    StaffMessageDelivery.created_at,
                    StaffMessageDelivery.id,
                ),
            ).label("rank"),
        )
        .join(Platform, and_(
            Platform.id == HiddenShadowRun.platform_id,
            Platform.project_id == HiddenShadowRun.project_id,
        ))
        .join(StaffMessageDelivery, and_(
            StaffMessageDelivery.project_id == HiddenShadowRun.project_id,
            StaffMessageDelivery.visitor_id == HiddenShadowRun.visitor_id,
            StaffMessageDelivery.source_message_id
            == HiddenShadowRun.source_message_id,
        ))
        .where(
            HiddenShadowRun.project_id == project_id,
            HiddenShadowRun.created_at >= window_start,
            HiddenShadowRun.created_at < window_end,
            HiddenShadowRun.completed_at < window_end,
            HiddenShadowRun.status == "ready",
            HiddenShadowRun.generation_version == GENERATION_VERSION,
            HiddenShadowRun.answer.is_not(None),
            HiddenShadowRun.answer_sha256.is_not(None),
            HiddenShadowRun.knowledge_evidence_sha256.is_not(None),
            HiddenShadowRun.ai_execution_id.is_not(None),
            HiddenShadowRun.redacted_at.is_(None),
            HiddenShadowRun.source_message_seq > 0,
            ~visible_draft,
            Platform.type == "wecom",
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            StaffMessageDelivery.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
            StaffMessageDelivery.created_at > HiddenShadowRun.completed_at,
            StaffMessageDelivery.created_at < window_end,
            StaffMessageDelivery.updated_at < window_end,
            StaffMessageDelivery.assist_draft_id.is_(None),
            StaffMessageDelivery.external_status == "sent",
            StaffMessageDelivery.payload["type"].as_integer().in_((1, 12)),
            func.length(func.trim(
                StaffMessageDelivery.payload["content"].as_string()
            )) > 0,
        )
        .subquery()
    )
    query = (
        select(HiddenShadowRun, StaffMessageDelivery)
        .join(ranked, ranked.c.candidate_id == HiddenShadowRun.id)
        .join(StaffMessageDelivery,
              StaffMessageDelivery.id == ranked.c.delivery_id)
        .where(ranked.c.rank == 1)
        .order_by(HiddenShadowRun.id)
    )
    rows = db.execute(
        query.execution_options(stream_results=True)
    ).yield_per(1000)
    try:
        for candidate, delivery in rows:
            reply = delivery.payload.get("content")
            if not isinstance(reply, str) or not reply.strip():
                continue
            if not candidate.answer or not candidate.answer_sha256:
                continue
            answer_hash = sha256(candidate.answer.encode()).hexdigest()
            if answer_hash != candidate.answer_sha256:
                continue
            yield HiddenShadowComparison(
                candidate_id=candidate.id,
                visitor_id=candidate.visitor_id,
                staff_delivery_id=delivery.id,
                source_message_id=candidate.source_message_id,
                source_content_sha256=candidate.source_content_sha256,
                candidate_sha256=candidate.answer_sha256,
                staff_reply_sha256=sha256(reply.strip().encode()).hexdigest(),
                candidate_completed_at=candidate.completed_at,
                staff_reply_created_at=delivery.created_at,
            )
    finally:
        rows.close()
