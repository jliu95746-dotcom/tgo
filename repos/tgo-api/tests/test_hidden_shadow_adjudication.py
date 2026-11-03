"""A third administrator resolves only frozen independent review disputes."""

from uuid import uuid4

import pytest

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.schemas.hidden_shadow_review import HiddenShadowReviewRequest
from app.schemas.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationRequest,
)
from app.services.hidden_shadow_review import HiddenShadowReviewService
from app.services.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationService,
)
from tests.test_hidden_shadow_review import frozen_case as shared_frozen_case

frozen_case = shared_frozen_case


def _review_pair(db, project, protocol_id, candidate_id, second_verdict):
    service = HiddenShadowReviewService(db)
    first_reviewer, second_reviewer = uuid4(), uuid4()
    service.submit(
        project, protocol_id, candidate_id, first_reviewer,
        HiddenShadowReviewRequest(
            request_id=uuid4(), verdict="acceptable",
        ),
    )
    service.submit(
        project, protocol_id, candidate_id, second_reviewer,
        HiddenShadowReviewRequest(
            request_id=uuid4(), verdict=second_verdict,
            reason_code=("wrong_fact" if second_verdict != "acceptable"
                         else None),
        ),
    )
    return first_reviewer, second_reviewer


def test_disputed_case_requires_third_independent_adjudicator(frozen_case):
    db, project, coordinator, protocol, case, _run, delivery = frozen_case
    service = HiddenShadowAdjudicationService(db)
    with pytest.raises(TGOAPIException) as premature:
        service.review_pair(project, protocol.id, case.candidate_id)
    assert premature.value.status_code == 409
    reviewer_one, reviewer_two = _review_pair(
        db, project, protocol.id, case.candidate_id, "minor_issue",
    )
    pair = service.review_pair(project, protocol.id, case.candidate_id)
    assert {row.verdict for row in pair.reviews} == {
        "acceptable", "minor_issue",
    }
    assert pair.adjudication is None
    assert len(pair.review_set_sha256) == 64
    request = HiddenShadowAdjudicationRequest(
        request_id=uuid4(), expected_review_sha256=pair.review_set_sha256,
        verdict="minor_issue", reason_code="wrong_fact",
        note="两个结论不同，资料需要补全。",
    )
    for denied_actor in (reviewer_one, reviewer_two, delivery.staff_id):
        with pytest.raises(TGOAPIException) as denied:
            service.submit(
                project, protocol.id, case.candidate_id,
                denied_actor, request,
            )
        assert denied.value.status_code == 403
    with pytest.raises(TGOAPIException) as stale:
        service.submit(
            project, protocol.id, case.candidate_id, coordinator,
            request.model_copy(update={
                "expected_review_sha256": "0" * 64,
            }),
        )
    assert stale.value.status_code == 409
    receipt = service.submit(
        project, protocol.id, case.candidate_id, coordinator, request,
    )
    assert receipt.review_set_sha256 == pair.review_set_sha256
    assert service.submit(
        project, protocol.id, case.candidate_id, coordinator, request,
    ).id == receipt.id
    after = service.review_pair(project, protocol.id, case.candidate_id)
    assert after.adjudication is not None
    assert after.adjudication.id == receipt.id
    with pytest.raises(TGOAPIException) as conflicting_request:
        service.submit(
            project, protocol.id, case.candidate_id, coordinator,
            request.model_copy(update={"verdict": "acceptable"}),
        )
    assert conflicting_request.value.status_code == 409
    with pytest.raises(TGOAPIException) as second_decision:
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            request.model_copy(update={"request_id": uuid4()}),
        )
    assert second_decision.value.status_code == 409
    review = db.get(HiddenShadowCaseReview, pair.reviews[0].id)
    assert review is not None
    review.note = "事后改写的依据"
    db.commit()
    with pytest.raises(TGOAPIException) as changed_evidence:
        service.review_pair(project, protocol.id, case.candidate_id)
    assert changed_evidence.value.status_code == 409


def test_agreed_reviews_cannot_be_adjudicated(frozen_case):
    db, project, coordinator, protocol, case, _run, _delivery = frozen_case
    _review_pair(db, project, protocol.id, case.candidate_id, "acceptable")
    service = HiddenShadowAdjudicationService(db)
    pair = service.review_pair(project, protocol.id, case.candidate_id)
    with pytest.raises(TGOAPIException) as agreed:
        service.submit(
            project, protocol.id, case.candidate_id, coordinator,
            HiddenShadowAdjudicationRequest(
                request_id=uuid4(),
                expected_review_sha256=pair.review_set_sha256,
                verdict="acceptable", note="两人意见一致，无需裁决。",
            ),
        )
    assert agreed.value.status_code == 409


def test_insufficient_evidence_cannot_be_adjudicated(frozen_case):
    db, project, coordinator, protocol, case, _run, _delivery = frozen_case
    _review_pair(
        db, project, protocol.id, case.candidate_id,
        "insufficient_evidence",
    )
    service = HiddenShadowAdjudicationService(db)
    pair = service.review_pair(project, protocol.id, case.candidate_id)
    with pytest.raises(TGOAPIException) as insufficient:
        service.submit(
            project, protocol.id, case.candidate_id, coordinator,
            HiddenShadowAdjudicationRequest(
                request_id=uuid4(),
                expected_review_sha256=pair.review_set_sha256,
                verdict="acceptable", note="证据不足，不能按正确回答裁决。",
            ),
        )
    assert insufficient.value.status_code == 409
