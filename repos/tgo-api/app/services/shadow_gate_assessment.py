"""Assess official G4 review evidence without approving or enabling replies."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.models.shadow_review_protocol import ShadowReviewProtocolCandidate
from app.schemas.shadow_gate_assessment import (
    ShadowGateAssessment, ShadowGateBlocker,
)
from app.services.shadow_manifest_integrity import verify_shadow_manifest
from app.services.shadow_sample_service import ShadowSampleService
from app.services.shadow_sample_selection import WECOM_SAMPLE_ALGORITHM


class ShadowGateAssessmentService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def assess(
        self, project_id: UUID, protocol_id: UUID,
    ) -> ShadowGateAssessment:
        protocol = self._db.scalar(
            select(ShadowReviewProtocol).where(
                ShadowReviewProtocol.id == protocol_id,
                ShadowReviewProtocol.project_id == project_id,
            )
        )
        if protocol is None:
            raise TGOAPIException(
                "正式影子验收方案不存在",
                "SHADOW_PROTOCOL_NOT_FOUND",
                status_code=404,
            )

        blockers: list[ShadowGateBlocker] = []
        integrity_consistent: bool | None = None
        sample_count = ready_count = historical_severe_count = None
        if protocol.finalized_at is None:
            blockers.append("window_not_frozen")
        elif protocol.batch_id is None:
            blockers.append("empty_sample")
        else:
            try:
                integrity = verify_shadow_manifest(
                    self._db, project_id, protocol_id
                )
                integrity_consistent = integrity.consistent
            except TGOAPIException as exc:
                if exc.code != "SHADOW_PROTOCOL_ALGORITHM_UNSUPPORTED":
                    raise
                integrity_consistent = False
            if not integrity_consistent:
                blockers.append("manifest_inconsistent")
            try:
                sample = ShadowSampleService(self._db).get_batch(
                    project_id, protocol.batch_id
                )
            except TGOAPIException as exc:
                if exc.code != "SHADOW_SAMPLE_NOT_FOUND":
                    raise
                sample = None
            if sample is None:
                if "manifest_inconsistent" not in blockers:
                    blockers.append("manifest_inconsistent")
            else:
                sample_count = sample.sample_count
                ready_count = sample.ready_count
                candidate_severe_count = self._db.scalar(
                    select(
                        func.count(func.distinct(ShadowDraftReview.draft_id))
                    )
                    .join(
                        ShadowReviewProtocolCandidate,
                        ShadowReviewProtocolCandidate.draft_id
                        == ShadowDraftReview.draft_id,
                    )
                    .where(
                        ShadowReviewProtocolCandidate.protocol_id
                        == protocol.id,
                        ShadowDraftReview.project_id == project_id,
                        ShadowDraftReview.verdict == "severe_error",
                    )
                ) or 0
                historical_severe_count = max(
                    sample.ever_severe_count, candidate_severe_count,
                )
                if sample_count == 0:
                    blockers.append("empty_sample")
                if historical_severe_count:
                    blockers.append("severe_history")
                if (
                    sample.missing_drafts_count
                    or sample.insufficient_case_count
                ):
                    blockers.append("evidence_missing")
                if sample.ready_count != sample.sample_count:
                    blockers.append("review_incomplete")

        if protocol.algorithm != WECOM_SAMPLE_ALGORITHM:
            blockers.append("official_channel_required")
        elif sample_count:
            # The v2 WeCom population requires a draft linked to the staff
            # delivery. Those drafts were exposed to staff before the reply,
            # so the review cannot establish independent shadow behavior.
            blockers.append("independent_shadow_missing")

        return ShadowGateAssessment(
            protocol_id=protocol.id,
            batch_id=protocol.batch_id,
            status="blocked" if blockers else "review_complete",
            blockers=tuple(blockers),
            can_request_g4_signoff=not blockers,
            integrity_consistent=integrity_consistent,
            sample_count=sample_count,
            ready_count=ready_count,
            historical_severe_count=historical_severe_count,
            assessed_at=datetime.now(timezone.utc),
        )
