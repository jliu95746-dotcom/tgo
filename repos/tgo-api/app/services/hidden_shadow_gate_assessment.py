"""Fail-closed G4 readiness for a frozen independent WeCom shadow sample."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_adjudication import HiddenShadowAdjudication
from app.models.hidden_shadow_protocol import HiddenShadowProtocolCase
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.hidden_shadow_gate_assessment import (
    HiddenShadowGateAssessment, HiddenShadowGateBlocker,
)
from app.services.hidden_shadow_adjudication import _review_set_digest
from app.services.hidden_shadow_protocol import HiddenShadowProtocolService
from app.services.hidden_shadow_review import HiddenShadowReviewService, _utc


class HiddenShadowGateAssessmentService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._protocols = HiddenShadowProtocolService(db)
        self._reviews = HiddenShadowReviewService(db)

    def assess(
        self, project_id: UUID, protocol_id: UUID,
    ) -> HiddenShadowGateAssessment:
        protocol = self._protocols.get(project_id, protocol_id)
        blockers: list[HiddenShadowGateBlocker] = []
        integrity_consistent: bool | None = None
        completed_cases: int | None = None
        severe_cases: int | None = None
        insufficient_cases: int | None = None
        disputed_cases: int | None = None
        adjudicated_cases: int | None = None
        unresolved_disputes: int | None = None
        review_evidence_sha256: str | None = None

        if protocol.finalized_at is None:
            blockers.append("window_not_frozen")
        else:
            integrity_consistent = self._protocols.verify_integrity(
                project_id, protocol_id,
            ).consistent
            if not integrity_consistent:
                blockers.append("manifest_inconsistent")
            if not protocol.sample_count:
                blockers.append("empty_sample")
            if not blockers:
                try:
                    summary = self._reviews.summary(project_id, protocol_id)
                except TGOAPIException as exc:
                    if exc.code != "HIDDEN_REVIEW_EVIDENCE_CHANGED":
                        raise
                    blockers.append("evidence_changed")
                else:
                    completed_cases = summary.completed_cases
                    if not summary.review_complete:
                        blockers.append("review_incomplete")
                    else:
                        severe_cases = summary.severe_cases
                        insufficient_cases = summary.insufficient_cases
                        disputed_cases = summary.disputed_cases
                        assert severe_cases is not None
                        assert insufficient_cases is not None
                        assert disputed_cases is not None
                        if insufficient_cases:
                            blockers.append("evidence_missing")
                        try:
                            (
                                adjudicated_cases, unresolved_disputes,
                                adjudicated_severe, review_evidence_sha256,
                            ) = self._review_evidence(
                                project_id, protocol_id,
                                protocol.finalized_at,
                            )
                        except TGOAPIException as exc:
                            if exc.code == "HIDDEN_GATE_EVIDENCE_MISSING":
                                blockers.append("evidence_missing")
                            elif exc.code == "HIDDEN_GATE_EVIDENCE_CHANGED":
                                blockers.append("evidence_changed")
                            else:
                                raise
                        else:
                            severe_cases += adjudicated_severe
                            if unresolved_disputes:
                                blockers.append("dispute_unresolved")
                        if severe_cases:
                            blockers.append("severe_history")

        return HiddenShadowGateAssessment(
            protocol_id=protocol_id,
            status="blocked" if blockers else "review_complete",
            blockers=tuple(blockers),
            can_request_g4_signoff=not blockers,
            integrity_consistent=integrity_consistent,
            sample_count=protocol.sample_count,
            completed_cases=completed_cases,
            severe_cases=severe_cases,
            insufficient_cases=insufficient_cases,
            disputed_cases=disputed_cases,
            adjudicated_cases=adjudicated_cases,
            unresolved_disputes=unresolved_disputes,
            review_evidence_sha256=review_evidence_sha256,
            assessed_at=datetime.now(timezone.utc),
        )

    def _review_evidence(
        self, project_id: UUID, protocol_id: UUID,
        finalized_at: datetime,
    ) -> tuple[int, int, int, str]:
        cases = self._db.scalars(select(HiddenShadowProtocolCase).where(
            HiddenShadowProtocolCase.protocol_id == protocol_id,
            HiddenShadowProtocolCase.selected_position.is_not(None),
        ).order_by(HiddenShadowProtocolCase.selected_position)).all()
        reviews = self._db.scalars(select(HiddenShadowCaseReview).where(
            HiddenShadowCaseReview.project_id == project_id,
            HiddenShadowCaseReview.protocol_id == protocol_id,
        )).all()
        candidates = {
            row.id: row for row in self._db.scalars(
                select(HiddenShadowRun).where(
                    HiddenShadowRun.project_id == project_id,
                    HiddenShadowRun.id.in_(
                        case.candidate_id for case in cases
                    ),
                )
            ).all()
        }
        adjudications = self._db.scalars(
            select(HiddenShadowAdjudication).where(
                HiddenShadowAdjudication.project_id == project_id,
                HiddenShadowAdjudication.protocol_id == protocol_id,
            )
        ).all()
        by_case: dict[UUID, list[HiddenShadowCaseReview]] = {
            case.candidate_id: [] for case in cases
        }
        decisions = {row.candidate_id: row for row in adjudications}
        if len(decisions) != len(adjudications):
            self._changed()
        for review in reviews:
            if review.candidate_id not in by_case:
                self._changed()
            by_case[review.candidate_id].append(review)
        if any(candidate_id not in by_case for candidate_id in decisions):
            self._changed()

        digest = hashlib.sha256()
        adjudicated = unresolved = adjudicated_severe = 0
        for case in cases:
            pair = by_case[case.candidate_id]
            if len(pair) != 2 or pair[0].reviewer_id == pair[1].reviewer_id:
                self._changed()
            candidate = candidates.get(case.candidate_id)
            if candidate is None:
                self._changed()
            assert candidate is not None
            for review in pair:
                if (
                    review.source_verified_at is None
                    or review.source_message_seq is None
                    or review.source_content_sha256 is None
                ):
                    self._missing()
                assert review.source_verified_at is not None
                if (
                    review.source_message_seq != candidate.source_message_seq
                    or review.source_content_sha256
                    != case.source_content_sha256
                    or _utc(review.source_verified_at) < _utc(finalized_at)
                    or _utc(review.source_verified_at)
                    > _utc(review.created_at) + timedelta(seconds=30)
                ):
                    self._changed()
            review_sha = _review_set_digest(pair)
            verdicts = {row.verdict for row in pair}
            decision = decisions.get(case.candidate_id)
            disputed = len(verdicts) == 2
            if decision is not None:
                delivery = self._db.get(
                    StaffMessageDelivery, case.staff_delivery_id,
                )
                if (
                    not disputed
                    or "insufficient_evidence" in verdicts
                    or delivery is None
                    or delivery.project_id != project_id
                    or decision.adjudicator_id in {
                        pair[0].reviewer_id, pair[1].reviewer_id,
                        delivery.staff_id if delivery is not None else None,
                    }
                    or decision.review_set_sha256 != review_sha
                    or decision.evidence_sha256 != pair[0].evidence_sha256
                ):
                    self._changed()
                adjudicated += 1
                if decision.verdict == "severe_error" and (
                    "severe_error" not in verdicts
                ):
                    adjudicated_severe += 1
            elif disputed and "insufficient_evidence" not in verdicts:
                unresolved += 1
            record = {
                "candidate_id": str(case.candidate_id),
                "selected_position": case.selected_position,
                "review_set_sha256": review_sha,
                "adjudication": (
                    {
                        "id": str(decision.id),
                        "adjudicator_id": str(decision.adjudicator_id),
                        "verdict": decision.verdict,
                        "reason_code": decision.reason_code,
                        "note": decision.note,
                        "created_at": decision.created_at.isoformat(),
                    } if decision is not None else None
                ),
            }
            digest.update(json.dumps(
                record, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode())
            digest.update(b"\0")
        return adjudicated, unresolved, adjudicated_severe, digest.hexdigest()

    @staticmethod
    def _changed() -> None:
        raise TGOAPIException(
            "独立影子复核或裁决依据已变化，请重新核查",
            "HIDDEN_GATE_EVIDENCE_CHANGED", status_code=409,
        )

    @staticmethod
    def _missing() -> None:
        raise TGOAPIException(
            "独立影子复核缺少原消息核验证据",
            "HIDDEN_GATE_EVIDENCE_MISSING", status_code=409,
        )
