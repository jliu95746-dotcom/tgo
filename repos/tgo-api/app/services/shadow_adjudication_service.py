"""Resolve a frozen sample dispute without rewriting either human review."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.message_intent_result import MessageIntentResult
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_adjudication import ShadowReviewAdjudication
from app.models.shadow_review_batch import (
    ShadowReviewBatch,
    ShadowReviewBatchItem,
)
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_adjudication import (
    ShadowAdjudicationHistory,
    ShadowAdjudicationReceipt,
    ShadowAdjudicationRequest,
)


def review_set_digest(rows: Iterable[tuple[UUID, UUID, str]]) -> str:
    (
        'Hash each current review identity and verdict in stable reviewer '
        'order.'
    )
    digest = hashlib.sha256()
    for reviewer_id, review_id, verdict in sorted(
        rows, key=lambda row: row[0].bytes
    ):
        digest.update(reviewer_id.bytes)
        digest.update(review_id.bytes)
        digest.update(verdict.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class ReviewSnapshot:
    reviewer_ids: frozenset[UUID]
    verdicts: frozenset[str]
    sha256: str


def current_review_snapshot(
    db: Session, project_id: UUID, draft_id: UUID
) -> ReviewSnapshot:
    ranked = (
        select(
            ShadowDraftReview.id.label("review_id"),
            ShadowDraftReview.reviewer_id.label("reviewer_id"),
            ShadowDraftReview.verdict.label("verdict"),
            func.row_number()
            .over(
                partition_by=ShadowDraftReview.reviewer_id,
                order_by=(
                    ShadowDraftReview.created_at.desc(),
                    ShadowDraftReview.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            ShadowDraftReview.project_id == project_id,
            ShadowDraftReview.draft_id == draft_id,
            ShadowDraftReview.reviewer_id.is_not(None),
        )
        .subquery()
    )
    reviews = [
        (row.reviewer_id, row.review_id, row.verdict)
        for row in db.execute(select(ranked).where(ranked.c.rank == 1))
    ]
    return ReviewSnapshot(
        reviewer_ids=frozenset(row[0] for row in reviews),
        verdicts=frozenset(row[2] for row in reviews),
        sha256=review_set_digest(reviews),
    )


class ShadowAdjudicationService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_history(
        self,
        project_id: UUID,
        batch_id: UUID,
        draft_id: UUID,
        visitor_id: UUID,
        *,
        limit: int,
        before_id: UUID | None = None,
    ) -> ShadowAdjudicationHistory:
        member = self._db.scalar(
            select(ShadowReviewBatchItem.position)
            .join(
                ShadowReviewBatch,
                ShadowReviewBatch.id == ShadowReviewBatchItem.batch_id,
            )
            .where(
                ShadowReviewBatch.project_id == project_id,
                ShadowReviewBatchItem.batch_id == batch_id,
                ShadowReviewBatchItem.draft_id == draft_id,
                ShadowReviewBatchItem.visitor_id == visitor_id,
            )
        )
        if member is None:
            raise TGOAPIException(
                "该案例不属于当前项目的固定样本",
                "SHADOW_ADJUDICATION_SAMPLE_NOT_FOUND",
                status_code=404,
            )
        history_query = select(ShadowReviewAdjudication).where(
            ShadowReviewAdjudication.project_id == project_id,
            ShadowReviewAdjudication.batch_id == batch_id,
            ShadowReviewAdjudication.draft_id == draft_id,
            ShadowReviewAdjudication.visitor_id == visitor_id,
        )
        if before_id is not None:
            cursor = self._db.scalar(
                history_query.where(ShadowReviewAdjudication.id == before_id)
            )
            if cursor is None:
                raise TGOAPIException(
                    "裁决历史游标不属于当前样本",
                    "SHADOW_ADJUDICATION_CURSOR_NOT_FOUND",
                    status_code=404,
                )
            history_query = history_query.where(
                or_(
                    ShadowReviewAdjudication.created_at < cursor.created_at,
                    and_(
                        ShadowReviewAdjudication.created_at
                        == cursor.created_at,
                        ShadowReviewAdjudication.id < cursor.id,
                    ),
                )
            )
        rows = self._db.scalars(
            history_query.order_by(
                ShadowReviewAdjudication.created_at.desc(),
                ShadowReviewAdjudication.id.desc(),
            ).limit(limit + 1)
        ).all()
        visible = rows[:limit]
        return ShadowAdjudicationHistory(
            items=tuple(
                ShadowAdjudicationReceipt.model_validate(
                    row, from_attributes=True
                )
                for row in visible
            ),
            has_more=len(rows) > limit,
            next_cursor=visible[-1].id if len(rows) > limit else None,
        )

    def submit(
        self,
        project_id: UUID,
        adjudicator_id: UUID,
        request: ShadowAdjudicationRequest,
    ) -> ShadowAdjudicationReceipt:
        fingerprint = hashlib.sha256(
            request.model_dump_json(exclude={"request_id"}).encode()
        ).hexdigest()

        def prior() -> ShadowReviewAdjudication | None:
            row = self._db.scalar(
                select(ShadowReviewAdjudication).where(
                    ShadowReviewAdjudication.project_id == project_id,
                    ShadowReviewAdjudication.request_id == request.request_id,
                )
            )
            if row is not None and (
                row.request_fingerprint != fingerprint
                or row.adjudicator_id != adjudicator_id
            ):
                raise TGOAPIException(
                    "裁决请求编号已用于其他内容或操作者",
                    "SHADOW_ADJUDICATION_CONFLICT",
                    status_code=409,
                )
            return row

        existing = prior()
        if existing is not None:
            return ShadowAdjudicationReceipt.model_validate(
                existing, from_attributes=True
            )

        member = self._db.scalar(
            select(ShadowReviewBatchItem)
            .join(
                ShadowReviewBatch,
                ShadowReviewBatch.id == ShadowReviewBatchItem.batch_id,
            )
            .where(
                ShadowReviewBatch.project_id == project_id,
                ShadowReviewBatchItem.batch_id == request.batch_id,
                ShadowReviewBatchItem.draft_id == request.draft_id,
                ShadowReviewBatchItem.visitor_id == request.visitor_id,
            )
            .with_for_update()
        )
        if member is None:
            raise TGOAPIException(
                "该案例不属于当前项目的固定样本",
                "SHADOW_ADJUDICATION_SAMPLE_NOT_FOUND",
                status_code=404,
            )
        draft = self._db.scalar(
            select(AssistDraftRecord).where(
                AssistDraftRecord.id == request.draft_id,
                AssistDraftRecord.project_id == project_id,
                AssistDraftRecord.visitor_id == request.visitor_id,
            )
        )
        draft_text = (
            draft.response_data.get("draft") if draft is not None else None
        )
        if (
            draft is None
            or not draft.source_message_id
            or (draft.source_proof or {}).get("status") != "verified"
            or not isinstance(draft_text, str)
            or not draft_text.strip()
        ):
            raise TGOAPIException(
                "原案例或来源证据已不可核验",
                "SHADOW_ADJUDICATION_EVIDENCE_MISSING",
                status_code=409,
            )
        shadow_id = self._db.scalar(
            select(MessageIntentResult.id).where(
                MessageIntentResult.project_id == project_id,
                MessageIntentResult.visitor_id == request.visitor_id,
                MessageIntentResult.source_message_id
                == draft.source_message_id,
                MessageIntentResult.analysis_mode == "shadow",
            )
        )
        delivery = self._db.scalar(
            select(StaffMessageDelivery).where(
                StaffMessageDelivery.project_id == project_id,
                StaffMessageDelivery.visitor_id == request.visitor_id,
                StaffMessageDelivery.staff_id == draft.staff_id,
                StaffMessageDelivery.source_message_id
                == draft.source_message_id,
                StaffMessageDelivery.assist_draft_id == draft.id,
                StaffMessageDelivery.created_at >= draft.created_at,
                or_(
                    StaffMessageDelivery.external_status == "sent",
                    and_(
                        StaffMessageDelivery.external_status == "not_required",
                        StaffMessageDelivery.history_status == "sent",
                    ),
                ),
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
                "影子分类或已确认人工回复证据缺失",
                "SHADOW_ADJUDICATION_EVIDENCE_MISSING",
                status_code=409,
            )
        snapshot = current_review_snapshot(
            self._db, project_id, request.draft_id
        )
        if (
            adjudicator_id == draft.staff_id
            or adjudicator_id in snapshot.reviewer_ids
        ):
            raise TGOAPIException(
                "原客服或参与复核者不能裁决自己的案例",
                "SHADOW_ADJUDICATION_SELF_REVIEW",
                status_code=403,
            )
        if (
            len(snapshot.reviewer_ids) < 2
            or len(snapshot.verdicts) < 2
            or "insufficient_evidence" in snapshot.verdicts
        ):
            raise TGOAPIException(
                "当前案例没有可裁决的两人结论冲突",
                "SHADOW_ADJUDICATION_NOT_DISPUTED",
                status_code=409,
            )
        if snapshot.sha256 != request.expected_review_sha256:
            raise TGOAPIException(
                "复核依据已变化，请刷新后重新裁决",
                "SHADOW_ADJUDICATION_STALE",
                status_code=409,
            )
        row = ShadowReviewAdjudication(
            project_id=project_id,
            batch_id=request.batch_id,
            draft_id=request.draft_id,
            visitor_id=request.visitor_id,
            adjudicator_id=adjudicator_id,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            review_set_sha256=snapshot.sha256,
            verdict=request.verdict,
            reason_code=request.reason_code,
            note=request.note,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(row)
        try:
            self._db.commit()
            return ShadowAdjudicationReceipt.model_validate(
                row, from_attributes=True
            )
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is None:
                raise
            return ShadowAdjudicationReceipt.model_validate(
                existing, from_attributes=True
            )
