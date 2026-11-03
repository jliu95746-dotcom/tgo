"""A frozen independent sample must pass every G4 review gate."""

from uuid import uuid4

import pytest

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.models.hidden_shadow_run import HiddenShadowRun
from app.schemas.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationRequest,
)
from app.schemas.hidden_shadow_review import HiddenShadowReviewRequest
from app.services.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationService,
)
from app.services.hidden_shadow_gate_assessment import (
    HiddenShadowGateAssessmentService,
)
from app.services.hidden_shadow_protocol import _manifest_digests
from app.services.hidden_shadow_pairing import HiddenShadowComparison
from app.services.hidden_shadow_review import HiddenShadowReviewService
from tests.test_hidden_shadow_review import (
    frozen_case as shared_frozen_case, verified_source,
)

frozen_case = shared_frozen_case


def _seal_manifest(db, protocol, case):
    comparison = HiddenShadowComparison(
        candidate_id=case.candidate_id,
        visitor_id=case.visitor_id,
        staff_delivery_id=case.staff_delivery_id,
        source_message_id=case.source_message_id,
        source_content_sha256=case.source_content_sha256,
        candidate_sha256=case.candidate_sha256,
        staff_reply_sha256=case.staff_reply_sha256,
        candidate_completed_at=case.candidate_completed_at,
        staff_reply_created_at=case.staff_reply_created_at,
    )
    protocol.population_sha256, protocol.manifest_sha256 = (
        _manifest_digests([comparison])
    )
    db.commit()


def _two_reviews(db, project, protocol, case, second_verdict):
    service = HiddenShadowReviewService(db)
    run = db.get(HiddenShadowRun, case.candidate_id)
    assert run is not None
    for verdict in ("acceptable", second_verdict):
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict=verdict,
                reason_code=("wrong_fact" if verdict in (
                    "minor_issue", "severe_error"
                ) else None),
            ),
            source_proof=verified_source(protocol, case, run),
        )


def test_gate_rejects_legacy_review_without_source_proof(frozen_case):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    service = HiddenShadowReviewService(db)
    for _ in range(2):
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict="acceptable",
            ),
        )
    result = HiddenShadowGateAssessmentService(db).assess(
        project, protocol.id,
    )
    assert "evidence_missing" in result.blockers
    assert not result.can_request_g4_signoff


def test_gate_rejects_changed_source_proof_after_review(frozen_case):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "acceptable")
    service = HiddenShadowGateAssessmentService(db)
    assert service.assess(project, protocol.id).can_request_g4_signoff
    review = db.query(HiddenShadowCaseReview).first()
    assert review is not None
    review.source_message_seq = 999
    db.commit()
    changed = service.assess(project, protocol.id)
    assert "evidence_changed" in changed.blockers
    assert not changed.can_request_g4_signoff


def test_gate_requires_frozen_consistent_complete_independent_sample(
    frozen_case,
):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    service = HiddenShadowGateAssessmentService(db)
    with pytest.raises(TGOAPIException) as other_project:
        service.assess(uuid4(), protocol.id)
    assert other_project.value.status_code == 404

    not_ready = service.assess(project, protocol.id)
    assert "manifest_inconsistent" in not_ready.blockers
    assert not not_ready.can_request_g4_signoff

    _seal_manifest(db, protocol, case)
    incomplete = service.assess(project, protocol.id)
    assert incomplete.blockers == ("review_incomplete",)
    assert incomplete.severe_cases is None
    assert incomplete.review_evidence_sha256 is None

    _two_reviews(db, project, protocol, case, "acceptable")
    ready = service.assess(project, protocol.id)
    assert ready.status == "review_complete"
    assert ready.blockers == ()
    assert ready.can_request_g4_signoff
    assert ready.requires_live_channel_verification
    assert ready.severe_cases == 0
    assert ready.unresolved_disputes == 0
    assert len(ready.review_evidence_sha256 or "") == 64

    protocol.manifest_sha256 = "0" * 64
    db.commit()
    changed = service.assess(project, protocol.id)
    assert "manifest_inconsistent" in changed.blockers
    assert not changed.can_request_g4_signoff


def test_gate_requires_current_third_person_decision_for_dispute(frozen_case):
    db, project, coordinator, protocol, case, _run, delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "minor_issue")
    service = HiddenShadowGateAssessmentService(db)
    unresolved = service.assess(project, protocol.id)
    assert unresolved.blockers == ("dispute_unresolved",)
    assert unresolved.unresolved_disputes == 1

    adjudication = HiddenShadowAdjudicationService(db)
    pair = adjudication.review_pair(project, protocol.id, case.candidate_id)
    adjudication.submit(
        project, protocol.id, case.candidate_id, coordinator,
        HiddenShadowAdjudicationRequest(
            request_id=uuid4(), expected_review_sha256=pair.review_set_sha256,
            verdict="acceptable", note="经第三人核对两份独立结论，可接受。",
        ),
    )
    ready = service.assess(project, protocol.id)
    assert ready.can_request_g4_signoff
    assert ready.adjudicated_cases == 1
    assert ready.unresolved_disputes == 0

    delivery.payload = {"type": 1, "content": "事后改写的人工答复"}
    db.commit()
    damaged = service.assess(project, protocol.id)
    assert "evidence_changed" in damaged.blockers
    assert not damaged.can_request_g4_signoff


@pytest.mark.parametrize(
    ("second_verdict", "expected_blocker"),
    [
        ("severe_error", "severe_history"),
        ("insufficient_evidence", "evidence_missing"),
    ],
)
def test_gate_never_signs_off_serious_or_insufficient_review(
    frozen_case, second_verdict, expected_blocker,
):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, second_verdict)
    result = HiddenShadowGateAssessmentService(db).assess(
        project, protocol.id,
    )
    assert expected_blocker in result.blockers
    assert not result.can_request_g4_signoff


def test_gate_blocks_unfrozen_or_empty_protocol(frozen_case):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    service = HiddenShadowGateAssessmentService(db)
    protocol.finalized_at = None
    protocol.population_count = None
    protocol.sample_count = None
    protocol.population_sha256 = None
    protocol.manifest_sha256 = None
    db.commit()
    assert service.assess(project, protocol.id).blockers == (
        "window_not_frozen",
    )
    protocol.finalized_at = case.staff_reply_created_at
    protocol.population_count = 0
    protocol.sample_count = 0
    protocol.population_sha256 = "0" * 64
    protocol.manifest_sha256 = "0" * 64
    db.delete(case)
    db.commit()
    result = service.assess(project, protocol.id)
    assert "empty_sample" in result.blockers
    assert not result.can_request_g4_signoff


def test_adjudication_cannot_erase_historical_severe_finding(frozen_case):
    db, project, coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "severe_error")
    adjudication = HiddenShadowAdjudicationService(db)
    pair = adjudication.review_pair(project, protocol.id, case.candidate_id)
    adjudication.submit(
        project, protocol.id, case.candidate_id, coordinator,
        HiddenShadowAdjudicationRequest(
            request_id=uuid4(), expected_review_sha256=pair.review_set_sha256,
            verdict="acceptable", note="第三人认为候选答复没有严重问题。",
        ),
    )
    service = HiddenShadowGateAssessmentService(db)
    result = service.assess(project, protocol.id)
    assert result.blockers == ("severe_history",)
    assert not result.can_request_g4_signoff

    review = db.get(HiddenShadowCaseReview, pair.reviews[0].id)
    assert review is not None
    review.note = "事后修改的意见"
    db.commit()
    changed = service.assess(project, protocol.id)
    assert "evidence_changed" in changed.blockers
    assert not changed.can_request_g4_signoff


def test_third_person_severe_verdict_blocks_gate(frozen_case):
    db, project, coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "minor_issue")
    adjudication = HiddenShadowAdjudicationService(db)
    pair = adjudication.review_pair(project, protocol.id, case.candidate_id)
    adjudication.submit(
        project, protocol.id, case.candidate_id, coordinator,
        HiddenShadowAdjudicationRequest(
            request_id=uuid4(), expected_review_sha256=pair.review_set_sha256,
            verdict="severe_error", reason_code="unsafe_action",
            note="复核后确认该答复会导致不安全操作。",
        ),
    )
    result = HiddenShadowGateAssessmentService(db).assess(
        project, protocol.id,
    )
    assert "severe_history" in result.blockers
    assert not result.can_request_g4_signoff
