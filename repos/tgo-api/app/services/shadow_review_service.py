"""Pair saved shadow analyses with confirmed staff deliveries for review."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.core.exceptions import TGOAPIException
from app.models.message_intent_result import MessageIntentResult
from app.models.assist_draft_record import AssistDraftRecord
from app.schemas.assist_trace import AssistRunTrace
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_review import (
    ShadowComparison,
    ShadowComparisonsResponse,
    ShadowStaffReply,
    ShadowReviewState,
)
from app.services.shadow_review_audit import ShadowReviewAudit

MAX_REPLIES_PER_PAGE = 500


def _confirmed_reply(row: StaffMessageDelivery) -> bool:
    return bool(
        row.external_status == "sent"
        or (
            row.external_status == "not_required"
            and row.history_status == "sent"
        )
    )


def _generation_traces(value: object) -> tuple[AssistRunTrace, ...]:
    if not isinstance(value, list):
        return ()
    traces: list[AssistRunTrace] = []
    for entry in value[:12]:
        try:
            traces.append(AssistRunTrace.model_validate(entry))
        except ValidationError:
            continue
    return tuple(traces)


def pair_shadow_cases(
    project_id: UUID,
    visitor_id: UUID,
    intents: Sequence[MessageIntentResult],
    deliveries: Sequence[StaffMessageDelivery],
    drafts: Sequence[AssistDraftRecord] = (),
    reviews: Mapping[UUID, ShadowReviewState] | None = None,
) -> tuple[ShadowComparison, ...]:
    """Exclude uncertain sends and foreign/mismatched customer messages."""
    replies: dict[str, list[ShadowStaffReply]] = {}
    drafts_by_id = {row.id: row for row in drafts}
    for row in deliveries:
        if (
            row.project_id != project_id
            or row.visitor_id != visitor_id
            or not row.source_message_id
            or not _confirmed_reply(row)
            or row.payload.get("type") not in (1, 12)
        ):
            continue
        content = row.payload.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        linked = (
            drafts_by_id.get(row.assist_draft_id)
            if row.assist_draft_id
            else None
        )
        ai_draft = None
        ai_draft_id = None
        source_verified = False
        generation_trace: tuple[AssistRunTrace, ...] = ()
        if linked is not None and (
            linked.project_id == project_id
            and linked.visitor_id == visitor_id
            and linked.staff_id == row.staff_id
            and linked.source_message_id == row.source_message_id
            and linked.created_at <= row.created_at
        ):
            draft_value = linked.response_data.get("draft")
            if isinstance(draft_value, str) and draft_value.strip():
                ai_draft = draft_value
                ai_draft_id = linked.id
                source_verified = bool(
                    linked.source_proof
                    and linked.source_proof.get("status") == "verified"
                )
                generation_trace = _generation_traces(
                    linked.response_data.get("generation_trace")
                )
        replies.setdefault(row.source_message_id, []).append(
            ShadowStaffReply(
                client_msg_no=row.client_msg_no,
                content=content,
                ai_draft=ai_draft,
                ai_draft_id=ai_draft_id,
                source_verified=source_verified,
                generation_trace=generation_trace,
                review=(reviews or {}).get(ai_draft_id)
                if ai_draft_id
                else None,
                created_at=row.created_at,
            )
        )
    return tuple(
        ShadowComparison(
            source_message_id=intent.source_message_id,
            intent=intent.intent,
            confidence=intent.confidence,
            risk_level=intent.risk_level,
            recommended_route=intent.recommended_route,
            need_human=intent.need_human,
            created_at=intent.created_at,
            replies=tuple(replies.get(intent.source_message_id, ())),
        )
        for intent in intents
        if intent.project_id == project_id
        and intent.visitor_id == visitor_id
        and intent.analysis_mode == "shadow"
    )


class ShadowReviewService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_comparisons(
        self,
        project_id: UUID,
        visitor_id: UUID,
        reviewer_id: UUID,
        limit: int,
        *,
        focus_draft_id: UUID | None = None,
    ) -> ShadowComparisonsResponse:
        source_id: str | None = None
        if focus_draft_id is not None:
            source_id = self._db.scalar(
                select(AssistDraftRecord.source_message_id).where(
                    AssistDraftRecord.id == focus_draft_id,
                    AssistDraftRecord.project_id == project_id,
                    AssistDraftRecord.visitor_id == visitor_id,
                )
            )
            if not source_id:
                raise TGOAPIException(
                    "待复核草稿不存在", "SHADOW_DRAFT_NOT_FOUND",
                    status_code=404,
                )
        query = self._db.query(MessageIntentResult).filter(
            MessageIntentResult.project_id == project_id,
            MessageIntentResult.visitor_id == visitor_id,
            MessageIntentResult.analysis_mode == "shadow",
        )
        if source_id is not None:
            query = query.filter(
                MessageIntentResult.source_message_id == source_id
            )
        rows: list[MessageIntentResult] = (
            query.order_by(
                MessageIntentResult.created_at.desc(),
                MessageIntentResult.id.desc(),
            ).limit(limit + 1).all()
        )
        cases = rows[:limit]
        if not cases:
            return ShadowComparisonsResponse(
                items=(), has_more=False, reply_limit_reached=False
            )
        source_ids = tuple(row.source_message_id for row in cases)
        delivery_query = self._db.query(StaffMessageDelivery).filter(
            StaffMessageDelivery.project_id == project_id,
            StaffMessageDelivery.visitor_id == visitor_id,
            StaffMessageDelivery.source_message_id.in_(source_ids),
            or_(
                StaffMessageDelivery.external_status == "sent",
                and_(
                    StaffMessageDelivery.external_status == "not_required",
                    StaffMessageDelivery.history_status == "sent",
                ),
            ),
        )
        if focus_draft_id is not None:
            delivery_query = delivery_query.filter(
                StaffMessageDelivery.assist_draft_id == focus_draft_id
            )
        deliveries: list[StaffMessageDelivery] = (
            delivery_query.order_by(
                StaffMessageDelivery.created_at, StaffMessageDelivery.id
            ).limit(MAX_REPLIES_PER_PAGE + 1).all()
        )
        selected_deliveries = deliveries[:MAX_REPLIES_PER_PAGE]
        draft_ids = {
            row.assist_draft_id
            for row in selected_deliveries
            if row.assist_draft_id
        }
        drafts: list[AssistDraftRecord] = (
            (
                self._db.query(AssistDraftRecord)
                .filter(
                    AssistDraftRecord.project_id == project_id,
                    AssistDraftRecord.visitor_id == visitor_id,
                    AssistDraftRecord.id.in_(draft_ids),
                )
                .all()
            )
            if draft_ids
            else []
        )
        reviews = ShadowReviewAudit(self._db).latest_for_drafts(
            project_id, visitor_id, reviewer_id, {row.id for row in drafts}
        )
        return ShadowComparisonsResponse(
            items=pair_shadow_cases(
                project_id,
                visitor_id,
                cases,
                selected_deliveries,
                drafts,
                reviews,
            ),
            has_more=len(rows) > limit,
            reply_limit_reached=len(deliveries) > MAX_REPLIES_PER_PAGE,
        )
