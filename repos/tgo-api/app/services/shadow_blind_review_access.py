"""Keep official shadow sample conclusions hidden during independent review."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_batch import ShadowReviewBatchItem
from app.models.shadow_review_protocol import ShadowReviewProtocol


class ShadowBlindReviewAccess:
    def __init__(self, db: Session) -> None:
        self._db = db

    def require_result_access(self, project_id: UUID, viewer_id: UUID) -> None:
        """Coordinators may inspect results; reviewers must finish every sample."""
        protocols = self._db.scalars(
            select(ShadowReviewProtocol).where(
                ShadowReviewProtocol.project_id == project_id
            )
        ).all()
        for protocol in protocols:
            if protocol.creator_id == viewer_id:
                continue
            if protocol.finalized_at is None:
                self._deny_results()
            if protocol.batch_id is None:
                continue
            missing_review = self._db.scalar(
                select(ShadowReviewBatchItem.draft_id)
                .where(
                    ShadowReviewBatchItem.batch_id == protocol.batch_id,
                    ~select(ShadowDraftReview.id)
                    .where(
                        ShadowDraftReview.project_id == project_id,
                        ShadowDraftReview.reviewer_id == viewer_id,
                        ShadowDraftReview.draft_id
                        == ShadowReviewBatchItem.draft_id,
                    )
                    .exists(),
                )
                .limit(1)
            )
            if missing_review is not None:
                self._deny_results()

    def require_review_allowed(
        self,
        project_id: UUID,
        reviewer_id: UUID,
        draft: AssistDraftRecord,
        request_id: UUID,
        fingerprint: str,
    ) -> None:
        """The coordinator cannot grade their cohort; one official grade per case."""
        protocols = self._db.scalars(
            select(ShadowReviewProtocol)
            .where(ShadowReviewProtocol.project_id == project_id)
            .order_by(ShadowReviewProtocol.created_at, ShadowReviewProtocol.id)
            .with_for_update()
        ).all()
        for protocol in protocols:
            if protocol.finalized_at is None:
                in_cohort = (
                    protocol.window_start
                    <= draft.created_at
                    < protocol.window_end
                )
            elif protocol.batch_id is not None:
                in_cohort = (
                    self._db.scalar(
                        select(ShadowReviewBatchItem.draft_id).where(
                            ShadowReviewBatchItem.batch_id
                            == protocol.batch_id,
                            ShadowReviewBatchItem.draft_id == draft.id,
                        )
                    )
                    is not None
                )
            else:
                in_cohort = False
            if not in_cohort:
                continue
            if protocol.creator_id == reviewer_id:
                raise TGOAPIException(
                    "正式盲审的协调人不能复核本批案例",
                    "SHADOW_REVIEW_COORDINATOR_CONFLICT",
                    status_code=403,
                )
            prior_review = self._db.scalar(
                select(ShadowDraftReview)
                .where(
                    ShadowDraftReview.project_id == project_id,
                    ShadowDraftReview.reviewer_id == reviewer_id,
                    ShadowDraftReview.draft_id == draft.id,
                )
                .limit(1)
            )
            if prior_review is not None:
                if (
                    prior_review.request_id == request_id
                    and prior_review.request_fingerprint == fingerprint
                ):
                    continue
                raise TGOAPIException(
                    "正式盲审案例已完成本人复核，不能再次修改结论",
                    "SHADOW_REVIEW_ALREADY_SUBMITTED",
                    status_code=409,
                )

    @staticmethod
    def _deny_results() -> None:
        raise TGOAPIException(
            "正式盲审期间，请先完成本人对全部样本的独立复核",
            "SHADOW_BLIND_REVIEW_LOCKED",
            status_code=403,
        )
