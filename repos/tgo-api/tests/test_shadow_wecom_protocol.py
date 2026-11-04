"""Official WeCom G4 samples exclude website traffic and retain legacy manifests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.message_intent_result import MessageIntentResult
from app.models.platform import Platform
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_adjudication import ShadowReviewAdjudication
from app.models.shadow_gate_signoff import ShadowGateSignoff
from app.models.shadow_review_batch import ShadowReviewBatch, ShadowReviewBatchItem
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol, ShadowReviewProtocolCandidate,
)
from app.models.staff_message_delivery import StaffMessageDelivery
from app.models.visitor import Visitor
from app.schemas.shadow_sample import ShadowProtocolCreateRequest
from app.services.shadow_manifest_integrity import verify_shadow_manifest
from app.services.shadow_gate_assessment import ShadowGateAssessmentService
from app.services.shadow_gate_signoff import ShadowGateSignoffService
from app.schemas.shadow_gate_signoff import ShadowGateSignoffRequest
from app.services.shadow_review_protocol_service import ShadowReviewProtocolService
from tests.test_shadow_sample_service import add_case


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (
        Platform, Visitor, AssistDraftRecord, MessageIntentResult,
        StaffMessageDelivery, ShadowDraftReview, ShadowReviewAdjudication,
        ShadowReviewBatch,
        ShadowReviewBatchItem, ShadowReviewProtocol, ShadowReviewProtocolCandidate,
        ShadowGateSignoff,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_wecom_protocol_freezes_only_verified_wecom_cases(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    clock = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: clock["now"])
    project, staff = uuid4(), uuid4()
    wecom = Platform(project_id=project, type="wecom", is_active=True)
    website = Platform(project_id=project, type="website", is_active=True)
    db.add_all((wecom, website))
    db.flush()
    wecom_visitor = Visitor(
        project_id=project, platform_id=wecom.id,
        platform_open_id="wecom-test-visitor",
    )
    web_visitor = Visitor(
        project_id=project, platform_id=website.id,
        platform_open_id="web-test-visitor",
    )
    db.add_all((wecom_visitor, web_visitor))
    db.flush()
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    service = ShadowReviewProtocolService(db)
    protocol = service.register(project, uuid4(), ShadowProtocolCreateRequest(
        request_id=uuid4(), window_start=start, window_end=end,
        sample_size=10, target_channel="wecom_kf",
    ))
    wecom_case = add_case(
        db, project_id=project, visitor_id=wecom_visitor.id,
        staff_id=staff, platform_id=wecom.id,
        at=start + timedelta(minutes=10),
    )
    add_case(
        db, project_id=project, visitor_id=web_visitor.id,
        staff_id=staff, platform_id=website.id,
        at=start + timedelta(minutes=20),
    )
    add_case(
        db, project_id=project, visitor_id=wecom_visitor.id,
        staff_id=staff, platform_id=website.id,
        at=start + timedelta(minutes=30),
    )
    clock["now"] = end + timedelta(minutes=1)
    frozen = service.finalize(project)

    assert protocol.target_channel == "wecom_kf"
    assert frozen.population_count == 1
    assert frozen.batch_id is not None
    assert [row.draft_id for row in db.query(ShadowReviewProtocolCandidate).all()] == [
        wecom_case.id,
    ]
    assert verify_shadow_manifest(db, project, protocol.id).consistent

    db.add_all(
        ShadowDraftReview(
            project_id=project, visitor_id=wecom_visitor.id,
            draft_id=wecom_case.id, reviewer_id=uuid4(),
            request_id=uuid4(), request_fingerprint="a" * 64,
            verdict="acceptable",
        )
        for _ in range(2)
    )
    db.commit()
    readiness = ShadowGateAssessmentService(db).assess(project, protocol.id)
    assert readiness.blockers == ("independent_shadow_missing",)
    assert not readiness.can_request_g4_signoff
    with pytest.raises(TGOAPIException) as blocked:
        ShadowGateSignoffService(db).request(
            project, protocol.id, uuid4(), ShadowGateSignoffRequest(
                request_id=uuid4(), evidence_reference="WECOM-REHEARSAL-001",
                live_case_count=1, note="已核对正式企业微信客服案例的人工回复。",
            ),
        )
    assert blocked.value.code == "SHADOW_GATE_NOT_READY"

    wecom_visitor.platform_id = website.id
    db.commit()
    assert not verify_shadow_manifest(db, project, protocol.id).consistent
    assert "manifest_inconsistent" in ShadowGateAssessmentService(db).assess(
        project, protocol.id
    ).blockers


def test_legacy_protocol_response_keeps_all_channel_label(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)
    project_id = uuid4()
    protocol = ShadowReviewProtocolService(db).register(
        project_id, uuid4(), ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=now + timedelta(hours=1),
            window_end=now + timedelta(hours=2),
            sample_size=1,
        ),
    )
    assert protocol.target_channel == "all"
    readiness = ShadowGateAssessmentService(db).assess(
        project_id, protocol.id
    )
    assert readiness.blockers == (
        "window_not_frozen", "official_channel_required"
    )
