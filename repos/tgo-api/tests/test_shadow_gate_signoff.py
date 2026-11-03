"""A human G4 decision is an auditable, two-person record, not an auto-reply switch."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.shadow_gate_signoff import ShadowGateSignoff
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.schemas.shadow_gate_assessment import ShadowGateAssessment
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest,
    ShadowGateSignoffRequest,
)
from app.services.shadow_gate_assessment import ShadowGateAssessmentService
from app.services.shadow_gate_signoff import ShadowGateSignoffService
from app.services.shadow_sample_service import ShadowSampleService
from app.services import shadow_gate_signoff as signoff_module
from app.services.shadow_sample_selection import WECOM_SAMPLE_ALGORITHM


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ShadowReviewProtocol.__table__.create(engine)
    ShadowGateSignoff.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def case(db, monkeypatch):
    project_id, requester_id, reviewer_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    protocol = ShadowReviewProtocol(
        id=uuid4(), project_id=project_id, creator_id=requester_id,
        request_id=uuid4(), request_fingerprint="a" * 64,
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(hours=1),
        sample_size=2, algorithm=WECOM_SAMPLE_ALGORITHM,
        random_seed="b" * 32,
        population_count=2, population_sha256="c" * 64,
        manifest_sha256="d" * 64, batch_id=uuid4(),
        created_at=now - timedelta(hours=3), finalized_at=now,
    )
    db.add(protocol)
    db.commit()
    blockers = {"value": (), "review_sha": "e" * 64}

    def assess(_self, _project_id, _protocol_id):
        return ShadowGateAssessment(
            protocol_id=protocol.id, batch_id=protocol.batch_id,
            status="blocked" if blockers["value"] else "review_complete",
            blockers=blockers["value"],
            can_request_g4_signoff=not blockers["value"],
            integrity_consistent=True, sample_count=1, ready_count=1,
            historical_severe_count=0, assessed_at=now,
        )

    monkeypatch.setattr(ShadowGateAssessmentService, "assess", assess)
    draft_id = uuid4()
    monkeypatch.setattr(
        ShadowSampleService, "get_batch",
        lambda _self, _project_id, _batch_id: SimpleNamespace(items=(
            SimpleNamespace(
                position=1, draft_id=draft_id,
                reviewer_count=2, review_set_sha256=None,
                review_status="agreed", adjudication_verdict=None,
            ),
        )),
    )
    monkeypatch.setattr(
        signoff_module, "current_review_snapshot",
        lambda _db, _project_id, _draft_id: SimpleNamespace(
            sha256=blockers["review_sha"]
        ),
    )
    return project_id, requester_id, reviewer_id, protocol, blockers


def request_payload():
    return ShadowGateSignoffRequest(
        request_id=uuid4(), evidence_reference="WECOM-REHEARSAL-2026-10",
        live_case_count=3, note="已核对企业微信真实渠道影子样本及人工回复。",
    )


def test_request_requires_ready_protocol_and_is_idempotent(db, case):
    project, requester, _, protocol, blockers = case
    service = ShadowGateSignoffService(db)
    request = request_payload()
    blockers["value"] = ("review_incomplete",)
    with pytest.raises(TGOAPIException) as denied:
        service.request(project, protocol.id, requester, request)
    assert denied.value.code == "SHADOW_GATE_NOT_READY"

    blockers["value"] = ()
    receipt = service.request(project, protocol.id, requester, request)
    assert receipt.status == "pending"
    assert receipt.manifest_sha256 == "d" * 64
    assert receipt.requester_id == requester
    assert service.request(project, protocol.id, requester, request).id == receipt.id
    assert len(db.query(ShadowGateSignoff).all()) == 1

    with pytest.raises(TGOAPIException) as conflict:
        service.request(project, protocol.id, requester, request_payload())
    assert conflict.value.code == "SHADOW_GATE_ALREADY_REQUESTED"


def test_decision_requires_other_admin_and_fresh_readiness(db, case):
    project, requester, reviewer, protocol, blockers = case
    service = ShadowGateSignoffService(db)
    receipt = service.request(project, protocol.id, requester, request_payload())
    decision = ShadowGateDecisionRequest(
        request_id=uuid4(), decision="accepted",
        note="已独立核对引用的真实渠道记录。",
        live_evidence_verified=True,
    )
    with pytest.raises(TGOAPIException) as self_review:
        service.decide(project, protocol.id, requester, decision)
    assert self_review.value.code == "SHADOW_GATE_SELF_SIGNOFF"

    blockers["value"] = ("severe_history",)
    with pytest.raises(TGOAPIException) as stale:
        service.decide(project, protocol.id, reviewer, decision)
    assert stale.value.code == "SHADOW_GATE_NOT_READY"

    blockers["value"] = ()
    signed = service.decide(project, protocol.id, reviewer, decision)
    assert signed.id == receipt.id
    assert signed.status == "accepted"
    assert signed.decider_id == reviewer
    assert service.decide(project, protocol.id, reviewer, decision).id == receipt.id
    with pytest.raises(TGOAPIException) as immutable:
        service.decide(project, protocol.id, reviewer, ShadowGateDecisionRequest(
            request_id=uuid4(), decision="rejected", note="另一份验收结论",
            live_evidence_verified=False,
        ))
    assert immutable.value.code == "SHADOW_GATE_ALREADY_DECIDED"


def test_acceptance_requires_live_evidence_and_project_scope(db, case):
    project, requester, reviewer, protocol, _ = case
    service = ShadowGateSignoffService(db)
    service.request(project, protocol.id, requester, request_payload())
    with pytest.raises(TGOAPIException) as denied:
        service.decide(project, protocol.id, reviewer, ShadowGateDecisionRequest(
            request_id=uuid4(), decision="accepted", note="尚未核对真实记录",
            live_evidence_verified=False,
        ))
    assert denied.value.code == "SHADOW_GATE_LIVE_EVIDENCE_REQUIRED"
    assert service.get(uuid4(), protocol.id) is None
    with pytest.raises(TGOAPIException):
        service.decide(uuid4(), protocol.id, reviewer, ShadowGateDecisionRequest(
            request_id=uuid4(), decision="rejected", note="无权限签收该项目",
            live_evidence_verified=False,
        ))


def test_changed_review_snapshot_cannot_be_accepted_but_can_be_rejected(db, case):
    project, requester, reviewer, protocol, blockers = case
    service = ShadowGateSignoffService(db)
    service.request(project, protocol.id, requester, request_payload())
    blockers["review_sha"] = "f" * 64
    with pytest.raises(TGOAPIException) as stale:
        service.decide(project, protocol.id, reviewer, ShadowGateDecisionRequest(
            request_id=uuid4(), decision="accepted",
            note="样本复核记录已改变，不能签收",
            live_evidence_verified=True,
        ))
    assert stale.value.code == "SHADOW_GATE_EVIDENCE_CHANGED"
    rejected = service.decide(project, protocol.id, reviewer, ShadowGateDecisionRequest(
        request_id=uuid4(), decision="rejected",
        note="复核依据已经改变，需要新一轮验收",
        live_evidence_verified=False,
    ))
    assert rejected.status == "rejected"
