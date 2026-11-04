"""Freeze a reproducible, tenant-scoped G4 shadow review population."""

from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_adjudication import ShadowReviewAdjudication
from app.models.shadow_review_batch import (
    ShadowReviewBatch,
    ShadowReviewBatchItem,
)
from app.schemas.shadow_sample import (
    ShadowSampleBatchList,
    ShadowSampleBatchResponse,
    ShadowSampleBatchSummary,
    ShadowSampleCreateRequest,
    ShadowSampleItem,
    ShadowSampleStatus,
)
from app.services.shadow_sample_selection import (
    SAMPLE_ALGORITHM,
    select_shadow_population,
)
from app.services.shadow_adjudication_service import review_set_digest


class ShadowSampleService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_batch(
        self,
        project_id: UUID,
        creator_id: UUID,
        request: ShadowSampleCreateRequest,
    ) -> ShadowSampleBatchResponse:
        fingerprint = hashlib.sha256(
            request.model_dump_json(exclude={"request_id"}).encode()
        ).hexdigest()

        def prior() -> ShadowReviewBatch | None:
            row = self._db.scalar(
                select(ShadowReviewBatch).where(
                    ShadowReviewBatch.project_id == project_id,
                    ShadowReviewBatch.request_id == request.request_id,
                )
            )
            if row is not None and row.request_fingerprint != fingerprint:
                raise TGOAPIException(
                    "同一抽样请求编号不能用于不同条件",
                    "SHADOW_SAMPLE_CONFLICT",
                    status_code=409,
                )
            return row

        existing = prior()
        if existing is not None:
            return self.get_batch(project_id, existing.id)

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=request.days)
        seed = secrets.token_hex(16)
        population = select_shadow_population(
            self._db,
            project_id,
            window_start=start,
            window_end=now,
            sample_size=request.sample_size,
            random_seed=seed,
        )
        if not population.chosen:
            raise TGOAPIException(
                "当前时间窗没有可抽样的已核验影子案例",
                "SHADOW_SAMPLE_EMPTY",
                status_code=409,
            )
        batch = ShadowReviewBatch(
            project_id=project_id,
            creator_id=creator_id,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            window_start=start,
            window_end=now,
            random_seed=seed,
            algorithm=SAMPLE_ALGORITHM,
            population_sha256=population.sha256,
            population_count=population.count,
            requested_size=request.sample_size,
            sample_count=len(population.chosen),
            created_at=now,
        )
        self._db.add(batch)
        try:
            self._db.flush()
            self._db.add_all(
                ShadowReviewBatchItem(
                    batch_id=batch.id,
                    position=position,
                    draft_id=draft_id,
                    visitor_id=visitor_id,
                )
                for position, (_, draft_id, visitor_id) in enumerate(
                    population.chosen, 1
                )
            )
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is None:
                raise
            return self.get_batch(project_id, existing.id)
        return self.get_batch(project_id, batch.id)

    def get_batch(self, project_id: UUID, batch_id: UUID) -> ShadowSampleBatchResponse:
        batch = self._db.scalar(
            select(ShadowReviewBatch).where(
                ShadowReviewBatch.id == batch_id,
                ShadowReviewBatch.project_id == project_id,
            )
        )
        if batch is None:
            raise TGOAPIException("抽样批次不存在", "SHADOW_SAMPLE_NOT_FOUND", status_code=404)
        items = self._db.scalars(
            select(ShadowReviewBatchItem)
            .where(ShadowReviewBatchItem.batch_id == batch.id)
            .order_by(ShadowReviewBatchItem.position)
        ).all()
        draft_ids = tuple(item.draft_id for item in items)
        existing_ids = set(
            self._db.scalars(
                select(AssistDraftRecord.id).where(
                    AssistDraftRecord.project_id == project_id,
                    AssistDraftRecord.id.in_(draft_ids),
                )
            ).all()
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
                ShadowDraftReview.draft_id.in_(draft_ids),
            )
            .subquery()
        )
        latest_verdicts = self._db.execute(
            select(ranked.c.draft_id, ranked.c.verdict).where(ranked.c.rank == 1)
        ).all()
        reviewed_count = sum(
            row.verdict in ("acceptable", "minor_issue", "severe_error")
            for row in latest_verdicts
        )
        insufficient_count = sum(
            row.verdict == "insufficient_evidence" for row in latest_verdicts
        )
        severe_ids = set(
            self._db.scalars(
                select(ShadowDraftReview.draft_id)
                .where(
                    ShadowDraftReview.project_id == project_id,
                    ShadowDraftReview.draft_id.in_(draft_ids),
                    ShadowDraftReview.verdict == "severe_error",
                )
                .distinct()
            )
        )
        reviewer_ranked = (
            select(
                ShadowDraftReview.draft_id.label("draft_id"),
                ShadowDraftReview.id.label("review_id"),
                ShadowDraftReview.reviewer_id.label("reviewer_id"),
                ShadowDraftReview.verdict.label("verdict"),
                func.row_number()
                .over(
                    partition_by=(
                        ShadowDraftReview.draft_id,
                        ShadowDraftReview.reviewer_id,
                    ),
                    order_by=(
                        ShadowDraftReview.created_at.desc(),
                        ShadowDraftReview.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                ShadowDraftReview.project_id == project_id,
                ShadowDraftReview.draft_id.in_(draft_ids),
                ShadowDraftReview.reviewer_id.is_not(None),
            )
            .subquery()
        )
        reviewer_verdicts: dict[UUID, list[tuple[UUID, UUID, str]]] = defaultdict(list)
        for row in self._db.execute(
            select(
                reviewer_ranked.c.draft_id,
                reviewer_ranked.c.reviewer_id,
                reviewer_ranked.c.review_id,
                reviewer_ranked.c.verdict,
            ).where(reviewer_ranked.c.rank == 1)
        ):
            reviewer_verdicts[row.draft_id].append(
                (row.reviewer_id, row.review_id, row.verdict)
            )

        adjudication_ranked = (
            select(
                ShadowReviewAdjudication.draft_id.label("draft_id"),
                ShadowReviewAdjudication.review_set_sha256.label("review_set_sha256"),
                ShadowReviewAdjudication.verdict.label("verdict"),
                func.row_number()
                .over(
                    partition_by=ShadowReviewAdjudication.draft_id,
                    order_by=(
                        ShadowReviewAdjudication.created_at.desc(),
                        ShadowReviewAdjudication.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                ShadowReviewAdjudication.project_id == project_id,
                ShadowReviewAdjudication.batch_id == batch.id,
            )
            .subquery()
        )
        latest_adjudications = {
            row.draft_id: row
            for row in self._db.execute(
                select(adjudication_ranked).where(adjudication_ranked.c.rank == 1)
            )
        }
        severe_ids.update(
            self._db.scalars(
                select(ShadowReviewAdjudication.draft_id)
                .where(
                    ShadowReviewAdjudication.project_id == project_id,
                    ShadowReviewAdjudication.batch_id == batch.id,
                    ShadowReviewAdjudication.verdict == "severe_error",
                )
                .distinct()
            )
        )

        def conflict_digest(draft_id: UUID) -> str | None:
            reviews = reviewer_verdicts[draft_id]
            verdicts = {row[2] for row in reviews}
            if (
                draft_id not in existing_ids
                or len(reviews) < 2
                or len(verdicts) < 2
                or "insufficient_evidence" in verdicts
            ):
                return None
            return review_set_digest(reviews)

        digests = {draft_id: conflict_digest(draft_id) for draft_id in draft_ids}
        current_adjudications = {
            draft_id: adjudication
            for draft_id, adjudication in latest_adjudications.items()
            if digests.get(draft_id) is not None
            and adjudication.review_set_sha256 == digests[draft_id]
        }
        current_severe_ids = {
            row.draft_id for row in latest_verdicts if row.verdict == "severe_error"
        }
        current_severe_ids.update(
            draft_id
            for draft_id, adjudication in current_adjudications.items()
            if adjudication.verdict == "severe_error"
        )

        def review_status(draft_id: UUID) -> ShadowSampleStatus:
            verdicts = [row[2] for row in reviewer_verdicts[draft_id]]
            if draft_id in severe_ids:
                return "severe_flagged"
            if draft_id not in existing_ids:
                return "missing"
            if "insufficient_evidence" in verdicts:
                return "insufficient"
            if not verdicts:
                return "unreviewed"
            if len(verdicts) == 1:
                return "needs_second"
            if len(set(verdicts)) == 1:
                return "agreed"
            return "adjudicated" if draft_id in current_adjudications else "disputed"

        statuses = {draft_id: review_status(draft_id) for draft_id in draft_ids}
        return ShadowSampleBatchResponse(
            id=batch.id,
            request_id=batch.request_id,
            created_at=batch.created_at,
            window_start=batch.window_start,
            window_end=batch.window_end,
            algorithm=batch.algorithm,
            random_seed=batch.random_seed,
            population_sha256=batch.population_sha256,
            population_count=batch.population_count,
            requested_size=batch.requested_size,
            sample_count=batch.sample_count,
            reviewed_count=reviewed_count,
            insufficient_count=insufficient_count,
            unreviewed_count=(batch.sample_count - reviewed_count - insufficient_count),
            current_severe_count=len(current_severe_ids),
            ever_severe_count=len(severe_ids),
            missing_drafts_count=len(draft_ids) - len(existing_ids),
            ready_count=sum(
                status in {"agreed", "adjudicated"} for status in statuses.values()
            ),
            resolved_count=len(current_adjudications),
            needs_second_count=sum(
                status == "needs_second" for status in statuses.values()
            ),
            disputed_count=sum(status == "disputed" for status in statuses.values()),
            insufficient_case_count=sum(
                status == "insufficient" for status in statuses.values()
            ),
            severe_flagged_count=sum(
                status == "severe_flagged" for status in statuses.values()
            ),
            items=tuple(
                ShadowSampleItem(
                    position=item.position,
                    draft_id=item.draft_id,
                    visitor_id=item.visitor_id,
                    reviewer_count=len(reviewer_verdicts[item.draft_id]),
                    review_status=statuses[item.draft_id],
                    review_set_sha256=digests[item.draft_id],
                    adjudication_verdict=(
                        current_adjudications[item.draft_id].verdict
                        if item.draft_id in current_adjudications
                        else None
                    ),
                )
                for item in items
            ),
        )

    def list_batches(self, project_id: UUID, *, limit: int) -> ShadowSampleBatchList:
        rows = self._db.scalars(
            select(ShadowReviewBatch)
            .where(ShadowReviewBatch.project_id == project_id)
            .order_by(
                ShadowReviewBatch.created_at.desc(),
                ShadowReviewBatch.id.desc(),
            )
            .limit(limit + 1)
        ).all()
        return ShadowSampleBatchList(
            items=tuple(
                ShadowSampleBatchSummary(
                    id=row.id,
                    created_at=row.created_at,
                    window_start=row.window_start,
                    window_end=row.window_end,
                    population_count=row.population_count,
                    requested_size=row.requested_size,
                    sample_count=row.sample_count,
                )
                for row in rows[:limit]
            ),
            has_more=len(rows) > limit,
        )
