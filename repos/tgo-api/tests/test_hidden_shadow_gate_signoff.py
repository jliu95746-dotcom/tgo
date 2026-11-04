"""Independent G4 signoff stays separate from reply routing."""

from uuid import uuid4

import pytest

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest, ShadowGateSignoffRequest,
)
from app.services.hidden_shadow_gate_signoff import (
    HiddenShadowGateSignoffService,
)
from tests.test_hidden_shadow_gate_assessment import (
    _seal_manifest, _two_reviews,
)
from tests.test_hidden_shadow_review import frozen_case as shared_frozen_case

frozen_case = shared_frozen_case


def _request(sample_count=1):
    return ShadowGateSignoffRequest(
        request_id=uuid4(), evidence_reference="wecom-kf-audit/2026-10-20",
        live_case_count=sample_count,
        note="已逐条核对企业微信原消息、AI 私有候选和人工实际发送回执。",
    )


def test_request_and_independent_decision_require_current_evidence(
    frozen_case,
):
    db, project, coordinator, protocol, case, _run, _delivery = frozen_case
    service = HiddenShadowGateSignoffService(db)
    requester, decider = uuid4(), uuid4()
    with pytest.raises(TGOAPIException) as premature:
        service.request(project, protocol.id, requester, _request())
    assert premature.value.status_code == 409

    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "acceptable")
    with pytest.raises(TGOAPIException) as wrong_live_count:
        service.request(project, protocol.id, requester, _request(2))
    assert wrong_live_count.value.status_code == 409

    request = _request()
    receipt = service.request(project, protocol.id, requester, request)
    assert receipt.status == "pending"
    assert receipt.current_evidence_valid is None
    assert receipt.review_evidence_sha256
    assert service.get(project, protocol.id).id == receipt.id
    assert (
        service.request(project, protocol.id, requester, request).id
        == receipt.id
    )
    with pytest.raises(TGOAPIException) as duplicate:
        service.request(project, protocol.id, coordinator, _request())
    assert duplicate.value.status_code == 409

    decision = ShadowGateDecisionRequest(
        request_id=uuid4(), decision="accepted",
        live_evidence_verified=True,
        note="另由独立管理员核对原消息、回复和官方回执。",
    )
    with pytest.raises(TGOAPIException) as self_signoff:
        service.decide(project, protocol.id, requester, decision)
    assert self_signoff.value.status_code == 403
    with pytest.raises(TGOAPIException) as no_live_proof:
        service.decide(
            project, protocol.id, decider,
            decision.model_copy(update={"live_evidence_verified": False}),
        )
    assert no_live_proof.value.status_code == 409
    accepted = service.decide(project, protocol.id, decider, decision)
    assert accepted.status == "accepted"
    assert accepted.current_evidence_valid is True
    assert (
        service.decide(project, protocol.id, decider, decision).id
        == receipt.id
    )
    with pytest.raises(TGOAPIException) as changed_decision:
        service.decide(
            project, protocol.id, decider,
            decision.model_copy(update={"request_id": uuid4()}),
        )
    assert changed_decision.value.status_code == 409
    assert service.get(uuid4(), protocol.id) is None


def test_accepted_signoff_reports_changed_review_digest_as_stale(frozen_case):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "acceptable")
    service = HiddenShadowGateSignoffService(db)
    service.request(project, protocol.id, uuid4(), _request())
    accepted = service.decide(
        project, protocol.id, uuid4(),
        ShadowGateDecisionRequest(
            request_id=uuid4(), decision="accepted",
            live_evidence_verified=True,
            note="已核对真实渠道原消息和发送回执。",
        ),
    )
    assert accepted.current_evidence_valid is True

    review = db.query(HiddenShadowCaseReview).first()
    assert review is not None
    review.note = "复核记录内容在签收后改变"
    db.commit()

    # The current reviews still pass readiness, but they no longer match
    # the evidence digest bound to the historical accepted signoff.
    assert service._assessment.assess(
        project, protocol.id,
    ).can_request_g4_signoff
    current = service.get(project, protocol.id)
    assert current is not None
    assert current.status == "accepted"
    assert current.current_evidence_valid is False


def test_changed_review_or_manifest_cannot_be_accepted(frozen_case):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    _seal_manifest(db, protocol, case)
    _two_reviews(db, project, protocol, case, "acceptable")
    service = HiddenShadowGateSignoffService(db)
    service.request(project, protocol.id, uuid4(), _request())
    protocol.manifest_sha256 = "0" * 64
    db.commit()
    with pytest.raises(TGOAPIException) as stale:
        service.decide(
            project, protocol.id, uuid4(),
            ShadowGateDecisionRequest(
                request_id=uuid4(), decision="accepted",
                live_evidence_verified=True,
                note="曾核对真实微信消息与官方回执。",
            ),
        )
    assert stale.value.status_code == 409
