"""Official shadow review readiness never implies production approval."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.message_intent_result import MessageIntentResult
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_adjudication import ShadowReviewAdjudication
from app.models.shadow_gate_signoff import ShadowGateSignoff
from app.models.shadow_review_batch import (
    ShadowReviewBatch, ShadowReviewBatchItem,
)
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol, ShadowReviewProtocolCandidate,
)
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_sample import ShadowProtocolCreateRequest
from app.services.shadow_gate_assessment import ShadowGateAssessmentService
from app.services.shadow_gate_signoff import ShadowGateSignoffService
from app.schemas.shadow_gate_signoff import ShadowGateSignoffRequest
from app.services.shadow_review_protocol_service import (
    ShadowReviewProtocolService,
)
from tests.test_shadow_sample_service import add_case


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (
        AssistDraftRecord, MessageIntentResult, StaffMessageDelivery,
        ShadowDraftReview, ShadowReviewAdjudication,
        ShadowReviewBatch, ShadowReviewBatchItem,
        ShadowReviewProtocol, ShadowReviewProtocolCandidate,
        ShadowGateSignoff,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def register(db, monkeypatch, *, sample_size=2):
    from app.services import shadow_review_protocol_service as protocol_module

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    clock = {"now": now}
    monkeypatch.setattr(protocol_module, "_utc_now", lambda: clock["now"])
    project_id, visitor_id, staff_id, creator_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    protocol = ShadowReviewProtocolService(db).register(
        project_id, creator_id, ShadowProtocolCreateRequest(
            request_id=uuid4(), window_start=start, window_end=end,
            sample_size=sample_size,
        ),
    )
    return clock, project_id, visitor_id, staff_id, protocol, start, end


def review(db, project_id, draft, reviewer_id, verdict):
    db.add(ShadowDraftReview(
        project_id=project_id, visitor_id=draft.visitor_id,
        draft_id=draft.id, reviewer_id=reviewer_id,
        request_id=uuid4(), request_fingerprint="a" * 64,
        verdict=verdict,
        reason_code="wrong_fact" if verdict == "severe_error" else None,
    ))
    db.commit()


def test_official_gate_requires_frozen_nonempty_complete_double_review(
    db, monkeypatch,
):
    clock, project, visitor, staff, protocol, start, end = register(
        db, monkeypatch,
    )
    gate = ShadowGateAssessmentService(db)
    scheduled = gate.assess(project, protocol.id)
    assert scheduled.status == "blocked"
    assert scheduled.blockers == (
        "window_not_frozen", "official_channel_required"
    )
    assert not scheduled.can_request_g4_signoff

    draft = add_case(db, project_id=project, visitor_id=visitor,
                     staff_id=staff, at=start + timedelta(minutes=10))
    clock["now"] = end + timedelta(seconds=1)
    frozen = ShadowReviewProtocolService(db).finalize(project)
    assert frozen.batch_id is not None
    pending = gate.assess(project, protocol.id)
    assert pending.blockers == (
        "review_incomplete", "official_channel_required"
    )
    assert pending.sample_count == 1
    assert pending.ready_count == 0

    review(db, project, draft, uuid4(), "acceptable")
    assert gate.assess(project, protocol.id).blockers == (
        "review_incomplete", "official_channel_required"
    )
    review(db, project, draft, uuid4(), "acceptable")
    ready = gate.assess(project, protocol.id)
    assert ready.status == "blocked"
    assert ready.blockers == ("official_channel_required",)
    assert not ready.can_request_g4_signoff
    assert ready.requires_live_channel_verification
    assert ready.integrity_consistent is True
    with pytest.raises(TGOAPIException) as legacy:
        ShadowGateSignoffService(db).request(
            project, protocol.id, uuid4(), ShadowGateSignoffRequest(
                request_id=uuid4(), evidence_reference="WECOM-REHEARSAL-001",
                live_case_count=1, note="真实渠道记录仍须另一位管理员核对。",
            ),
        )
    assert legacy.value.code == "SHADOW_GATE_OFFICIAL_CHANNEL_REQUIRED"


def test_historical_severe_or_tampered_manifest_never_looks_ready(
    db, monkeypatch,
):
    clock, project, visitor, staff, protocol, start, end = register(
        db, monkeypatch,
    )
    draft = add_case(db, project_id=project, visitor_id=visitor,
                     staff_id=staff, at=start + timedelta(minutes=10))
    clock["now"] = end + timedelta(seconds=1)
    ShadowReviewProtocolService(db).finalize(project)
    reviewer = uuid4()
    review(db, project, draft, reviewer, "severe_error")
    review(db, project, draft, reviewer, "acceptable")
    review(db, project, draft, uuid4(), "acceptable")
    gate = ShadowGateAssessmentService(db)
    severe = gate.assess(project, protocol.id)
    assert "severe_history" in severe.blockers
    assert not severe.can_request_g4_signoff

    candidate = db.query(ShadowReviewProtocolCandidate).filter_by(
        protocol_id=protocol.id
    ).one()
    candidate.visitor_id = uuid4()
    db.commit()
    corrupted = gate.assess(project, protocol.id)
    assert "manifest_inconsistent" in corrupted.blockers
    assert not corrupted.can_request_g4_signoff


def test_empty_or_foreign_protocol_cannot_request_signoff(db, monkeypatch):
    clock, project, _, _, protocol, _, end = register(db, monkeypatch)
    clock["now"] = end + timedelta(seconds=1)
    ShadowReviewProtocolService(db).finalize(project)
    gate = ShadowGateAssessmentService(db)
    empty = gate.assess(project, protocol.id)
    assert empty.blockers == ("empty_sample", "official_channel_required")
    assert not empty.can_request_g4_signoff
    with pytest.raises(TGOAPIException) as denied:
        gate.assess(uuid4(), protocol.id)
    assert denied.value.status_code == 404


def test_known_severe_error_outside_sample_blocks_signoff(db, monkeypatch):
    clock, project, visitor, staff, protocol, start, end = register(
        db, monkeypatch, sample_size=1,
    )
    drafts = [
        add_case(db, project_id=project, visitor_id=visitor,
                 staff_id=staff, at=start + timedelta(minutes=minute))
        for minute in (10, 20)
    ]
    clock["now"] = end + timedelta(seconds=1)
    frozen = ShadowReviewProtocolService(db).finalize(project)
    selected_id = db.query(ShadowReviewBatchItem.draft_id).filter_by(
        batch_id=frozen.batch_id,
    ).scalar()
    selected = next(draft for draft in drafts if draft.id == selected_id)
    unselected = next(draft for draft in drafts if draft.id != selected_id)
    review(db, project, selected, uuid4(), "acceptable")
    review(db, project, selected, uuid4(), "acceptable")
    review(db, project, unselected, uuid4(), "severe_error")

    result = ShadowGateAssessmentService(db).assess(project, protocol.id)
    assert result.ready_count == 1
    assert result.historical_severe_count == 1
    assert result.blockers == (
        "severe_history", "official_channel_required"
    )
    assert not result.can_request_g4_signoff
