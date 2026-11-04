"""Append-only G4 human review and conservative project-wide counts."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.message_intent_result import MessageIntentResult
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_review import (
    ShadowPendingReviewItem,
    ShadowPendingReviewQueue,
    ShadowReviewReceipt,
    ShadowReviewRequest,
    ShadowReviewState,
    ShadowReviewSummary,
)
from app.services.shadow_blind_review_access import ShadowBlindReviewAccess


def _confirmed_delivery() -> ColumnElement[bool]:
    return or_(
        StaffMessageDelivery.external_status == "sent",
        and_(
            StaffMessageDelivery.external_status == "not_required",
            StaffMessageDelivery.history_status == "sent",
        ),
    )


def _receipt(row: ShadowDraftReview) -> ShadowReviewReceipt:
    return ShadowReviewReceipt.model_validate(row, from_attributes=True)


class ShadowReviewAudit:
    def __init__(self, db: Session) -> None:
        self._db = db

    def pending_queue(
        self,
        project_id: UUID,
        reviewer_id: UUID,
        *,
        days: int,
        limit: int,
        after_draft_id: UUID | None = None,
    ) -> ShadowPendingReviewQueue:
        """Locate verified, confirmed, unreviewed drafts for admins."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        pending = select(
            AssistDraftRecord.id,
            AssistDraftRecord.visitor_id,
            AssistDraftRecord.created_at,
        ).where(
            AssistDraftRecord.project_id == project_id,
            AssistDraftRecord.created_at >= start,
            AssistDraftRecord.created_at < now,
            AssistDraftRecord.source_message_id.is_not(None),
            AssistDraftRecord.source_proof["status"].as_string() == "verified",
            func.length(
                func.trim(AssistDraftRecord.response_data["draft"].as_string())
            ) > 0,
            select(MessageIntentResult.id)
            .where(
                MessageIntentResult.project_id == AssistDraftRecord.project_id,
                MessageIntentResult.visitor_id == AssistDraftRecord.visitor_id,
                MessageIntentResult.source_message_id
                == AssistDraftRecord.source_message_id,
                MessageIntentResult.analysis_mode == "shadow",
            )
            .exists(),
            select(StaffMessageDelivery.id)
            .where(
                StaffMessageDelivery.project_id
                == AssistDraftRecord.project_id,
                StaffMessageDelivery.visitor_id
                == AssistDraftRecord.visitor_id,
                StaffMessageDelivery.staff_id == AssistDraftRecord.staff_id,
                StaffMessageDelivery.source_message_id
                == AssistDraftRecord.source_message_id,
                StaffMessageDelivery.assist_draft_id == AssistDraftRecord.id,
                StaffMessageDelivery.created_at
                >= AssistDraftRecord.created_at,
                _confirmed_delivery(),
                StaffMessageDelivery.payload["type"].as_integer().in_((1, 12)),
                func.length(
                    func.trim(
                        StaffMessageDelivery.payload["content"].as_string()
                    )
                ) > 0,
            )
            .exists(),
            ~select(ShadowDraftReview.id)
            .where(
                ShadowDraftReview.project_id == AssistDraftRecord.project_id,
                ShadowDraftReview.draft_id == AssistDraftRecord.id,
                ShadowDraftReview.reviewer_id == reviewer_id,
            )
            .exists(),
        )
        total = self._db.scalar(
            select(func.count()).select_from(pending.subquery())
        ) or 0
        if after_draft_id is not None:
            cursor = self._db.execute(
                select(
                    AssistDraftRecord.id, AssistDraftRecord.created_at
                ).where(
                    AssistDraftRecord.id == after_draft_id,
                    AssistDraftRecord.project_id == project_id,
                )
            ).one_or_none()
            if cursor is None:
                raise TGOAPIException(
                    "复核清单游标不存在", "SHADOW_QUEUE_CURSOR_NOT_FOUND",
                    status_code=404,
                )
            pending = pending.where(
                or_(
                    AssistDraftRecord.created_at > cursor.created_at,
                    and_(
                        AssistDraftRecord.created_at == cursor.created_at,
                        AssistDraftRecord.id > cursor.id,
                    ),
                )
            )
        rows = self._db.execute(
            pending.order_by(
                AssistDraftRecord.created_at, AssistDraftRecord.id
            ).limit(limit + 1)
        ).all()
        visible = rows[:limit]
        return ShadowPendingReviewQueue(
            items=tuple(
                ShadowPendingReviewItem(
                    draft_id=row.id,
                    visitor_id=row.visitor_id,
                    created_at=row.created_at,
                )
                for row in visible
            ),
            total_pending=total,
            next_cursor=visible[-1].id if len(rows) > limit else None,
        )

    def submit(
        self, project_id: UUID, reviewer_id: UUID, request: ShadowReviewRequest
    ) -> ShadowReviewReceipt:
        fingerprint = hashlib.sha256(
            request.model_dump_json(exclude={"request_id"}).encode()
        ).hexdigest()

        def prior() -> ShadowDraftReview | None:
            row: ShadowDraftReview | None = self._db.scalar(
                select(ShadowDraftReview).where(
                    ShadowDraftReview.project_id == project_id,
                    ShadowDraftReview.reviewer_id == reviewer_id,
                    ShadowDraftReview.request_id == request.request_id,
                )
            )
            if row is not None and row.request_fingerprint != fingerprint:
                raise TGOAPIException(
                    "同一复核请求编号不能用于不同内容",
                    code="SHADOW_REVIEW_CONFLICT",
                    status_code=409,
                )
            return row

        existing = prior()
        if existing is not None:
            return _receipt(existing)
        draft: AssistDraftRecord | None = self._db.scalar(
            select(AssistDraftRecord).where(
                AssistDraftRecord.id == request.draft_id,
                AssistDraftRecord.project_id == project_id,
                AssistDraftRecord.visitor_id == request.visitor_id,
            )
        )
        if draft is None:
            raise TGOAPIException(
                "未找到可复核的 AI 草稿",
                code="SHADOW_DRAFT_NOT_FOUND",
                status_code=404,
            )
        ShadowBlindReviewAccess(self._db).require_review_allowed(
            project_id, reviewer_id, draft, request.request_id, fingerprint
        )
        draft_text = draft.response_data.get("draft")
        if (
            not draft.source_message_id
            or not isinstance(draft_text, str)
            or not draft_text.strip()
        ):
            raise TGOAPIException(
                "草稿缺少客户消息编号或有效正文",
                code="SHADOW_REVIEW_NOT_ELIGIBLE",
                status_code=409,
            )
        shadow_id: UUID | None = self._db.scalar(
            select(MessageIntentResult.id).where(
                MessageIntentResult.project_id == project_id,
                MessageIntentResult.visitor_id == request.visitor_id,
                MessageIntentResult.source_message_id
                == draft.source_message_id,
                MessageIntentResult.analysis_mode == "shadow",
            )
        )
        delivery: StaffMessageDelivery | None = self._db.scalar(
            select(StaffMessageDelivery).where(
                StaffMessageDelivery.project_id == project_id,
                StaffMessageDelivery.visitor_id == request.visitor_id,
                StaffMessageDelivery.staff_id == draft.staff_id,
                StaffMessageDelivery.source_message_id
                == draft.source_message_id,
                StaffMessageDelivery.assist_draft_id == draft.id,
                StaffMessageDelivery.created_at >= draft.created_at,
                _confirmed_delivery(),
                StaffMessageDelivery.payload["type"].as_integer().in_((1, 12)),
                func.length(
                    func.trim(
                        StaffMessageDelivery.payload["content"].as_string()
                    )
                )
                > 0,
            )
        )
        payload = delivery.payload if delivery is not None else {}
        content = payload.get("content")
        if (
            shadow_id is None
            or delivery is None
            or payload.get("type") not in (1, 12)
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise TGOAPIException(
                "只可复核已配对且已确认发送的影子样本",
                code="SHADOW_REVIEW_NOT_ELIGIBLE",
                status_code=409,
            )
        row = ShadowDraftReview(
            project_id=project_id,
            visitor_id=request.visitor_id,
            draft_id=draft.id,
            reviewer_id=reviewer_id,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            verdict=request.verdict,
            reason_code=request.reason_code,
            note=request.note,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(row)
        try:
            self._db.commit()
            return _receipt(row)
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is None:
                raise
            return _receipt(existing)

    def latest_for_drafts(
        self,
        project_id: UUID,
        visitor_id: UUID,
        reviewer_id: UUID,
        draft_ids: set[UUID],
    ) -> dict[UUID, ShadowReviewState]:
        if not draft_ids:
            return {}
        ranked = (
            select(
                ShadowDraftReview.id.label("review_id"),
                func.row_number()
                .over(
                    partition_by=ShadowDraftReview.draft_id,
                    order_by=(
                        ShadowDraftReview.created_at.desc(),
                        ShadowDraftReview.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                ShadowDraftReview.project_id == project_id,
                ShadowDraftReview.visitor_id == visitor_id,
                ShadowDraftReview.reviewer_id == reviewer_id,
                ShadowDraftReview.draft_id.in_(draft_ids),
            )
            .subquery()
        )
        rows: Sequence[ShadowDraftReview] = self._db.scalars(
            select(ShadowDraftReview)
            .join(
                ranked,
                ShadowDraftReview.id == ranked.c.review_id,
            )
            .where(ranked.c.rank == 1)
        ).all()
        return {
            row.draft_id: ShadowReviewState.model_validate(
                {
                    "verdict": row.verdict,
                    "reason_code": row.reason_code,
                    "reviewed_at": row.created_at,
                }
            )
            for row in rows
        }

    def summary(self, project_id: UUID, *, days: int) -> ShadowReviewSummary:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        eligible = select(AssistDraftRecord.id).where(
            AssistDraftRecord.project_id == project_id,
            AssistDraftRecord.created_at >= start,
            AssistDraftRecord.created_at < now,
            AssistDraftRecord.source_message_id.is_not(None),
            func.length(
                func.trim(AssistDraftRecord.response_data["draft"].as_string())
            )
            > 0,
            select(MessageIntentResult.id)
            .where(
                MessageIntentResult.project_id == AssistDraftRecord.project_id,
                MessageIntentResult.visitor_id == AssistDraftRecord.visitor_id,
                MessageIntentResult.source_message_id
                == AssistDraftRecord.source_message_id,
                MessageIntentResult.analysis_mode == "shadow",
            )
            .exists(),
            select(StaffMessageDelivery.id)
            .where(
                StaffMessageDelivery.project_id
                == AssistDraftRecord.project_id,
                StaffMessageDelivery.visitor_id
                == AssistDraftRecord.visitor_id,
                StaffMessageDelivery.staff_id == AssistDraftRecord.staff_id,
                StaffMessageDelivery.source_message_id
                == AssistDraftRecord.source_message_id,
                StaffMessageDelivery.assist_draft_id == AssistDraftRecord.id,
                StaffMessageDelivery.created_at
                >= AssistDraftRecord.created_at,
                _confirmed_delivery(),
                StaffMessageDelivery.payload["type"].as_integer().in_((1, 12)),
                func.length(
                    func.trim(
                        StaffMessageDelivery.payload["content"].as_string()
                    )
                )
                > 0,
            )
            .exists(),
        )
        eligible_count = (
            self._db.scalar(
                select(func.count()).select_from(eligible.subquery())
            )
            or 0
        )
        verified = eligible.where(
            AssistDraftRecord.source_proof["status"].as_string() == "verified"
        )
        verified_count = (
            self._db.scalar(
                select(func.count()).select_from(verified.subquery())
            )
            or 0
        )
        ranked = (
            select(
                ShadowDraftReview.draft_id.label("draft_id"),
                ShadowDraftReview.verdict.label("verdict"),
                func.row_number()
                .over(
                    partition_by=ShadowDraftReview.draft_id,
                    order_by=(
                        ShadowDraftReview.created_at.desc(),
                        ShadowDraftReview.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                ShadowDraftReview.project_id == project_id,
                ShadowDraftReview.draft_id.in_(eligible),
            )
            .subquery()
        )
        counts = {
            verdict: count
            for verdict, count in self._db.execute(
                select(ranked.c.verdict, func.count())
                .where(ranked.c.rank == 1)
                .group_by(ranked.c.verdict)
            ).all()
        }
        evaluated = sum(
            counts.get(value, 0)
            for value in ("acceptable", "minor_issue", "severe_error")
        )
        insufficient = counts.get("insufficient_evidence", 0)
        ever_severe = (
            self._db.scalar(
                select(
                    func.count(func.distinct(ShadowDraftReview.draft_id))
                ).where(
                    ShadowDraftReview.project_id == project_id,
                    ShadowDraftReview.draft_id.in_(eligible),
                    ShadowDraftReview.verdict == "severe_error",
                )
            )
            or 0
        )
        verified_evaluated = (
            self._db.scalar(
                select(func.count())
                .select_from(ranked)
                .where(
                    ranked.c.rank == 1,
                    ranked.c.draft_id.in_(verified),
                    ranked.c.verdict.in_(
                        ("acceptable", "minor_issue", "severe_error")
                    ),
                )
            )
            or 0
        )
        verified_ever_severe = (
            self._db.scalar(
                select(
                    func.count(func.distinct(ShadowDraftReview.draft_id))
                ).where(
                    ShadowDraftReview.project_id == project_id,
                    ShadowDraftReview.draft_id.in_(verified),
                    ShadowDraftReview.verdict == "severe_error",
                )
            )
            or 0
        )
        return ShadowReviewSummary(
            window_start=start,
            window_end=now,
            eligible_drafts=eligible_count,
            evaluated_drafts=evaluated,
            insufficient_drafts=insufficient,
            unreviewed_drafts=eligible_count - evaluated - insufficient,
            ever_severe_drafts=ever_severe,
            current_severe_drafts=counts.get("severe_error", 0),
            verified_eligible_drafts=verified_count,
            verified_evaluated_drafts=verified_evaluated,
            verified_ever_severe_drafts=verified_ever_severe,
        )
