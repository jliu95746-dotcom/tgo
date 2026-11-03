"""G4 human reviews keep severe history for paired shadow drafts."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.message_intent_result import MessageIntentResult
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_batch import ShadowReviewBatch, ShadowReviewBatchItem
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_review import ShadowReviewRequest
from app.services.shadow_review_audit import ShadowReviewAudit
from app.services.shadow_review_service import ShadowReviewService


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(TextClause, "sqlite")
def compile_jsonb_default_sqlite(element, compiler, **kwargs):
    return compiler.visit_textclause(element, **kwargs).replace("::jsonb", "")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (
        AssistDraftRecord,
        MessageIntentResult,
        StaffMessageDelivery,
        ShadowDraftReview,
        ShadowReviewBatch,
        ShadowReviewBatchItem,
        ShadowReviewProtocol,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def eligible_case(
    db,
    *,
    project_id,
    visitor_id,
    staff_id,
    status="sent",
    mode="shadow",
    message_type=1,
    draft_text="AI 说可以直接退款",
    proof_status=None,
):
    source_id = f"customer-{uuid4()}"
    now = datetime.now(timezone.utc)
    draft = AssistDraftRecord(
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        request_id=uuid4(),
        source_message_id=source_id,
        request_fingerprint="f" * 64,
        response_data={"draft": draft_text},
        source_proof={"status": proof_status} if proof_status else None,
        created_at=now,
    )
    intent = MessageIntentResult(
        project_id=project_id,
        platform_id=uuid4(),
        visitor_id=visitor_id,
        source_message_id=source_id,
        analysis_mode=mode,
        intent="after_sale",
        confidence=0.9,
        entities={},
        risk_level="high",
        recommended_route="human_handoff",
        need_human=True,
        taxonomy_version="v1",
        routing_reason="risk_policy",
        classification_source="rule",
        classifier_version="v1",
        policy_version="v1",
        input_fingerprint="i" * 64,
        created_at=now,
    )
    db.add_all([draft, intent])
    db.flush()
    delivery = StaffMessageDelivery(
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        channel_id=f"{visitor_id}-vtr",
        channel_type=251,
        client_msg_no=f"staff-{uuid4()}",
        request_fingerprint="s" * 64,
        source_message_id=source_id,
        assist_draft_id=draft.id,
        payload={"type": message_type, "content": "请先提供订单号。"},
        external_status=status,
        history_status="sent",
        created_at=now,
    )
    db.add(delivery)
    db.commit()
    return draft


def test_shadow_review_replay_and_ever_severe_summary(db):
    project_id, visitor_id, staff_id, reviewer_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    draft = eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        proof_status="verified",
    )
    eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        status="unknown",
    )
    audit = ShadowReviewAudit(db)
    severe = ShadowReviewRequest(
        draft_id=draft.id,
        visitor_id=visitor_id,
        request_id=uuid4(),
        verdict="severe_error",
        reason_code="unsafe_action",
        note="草稿承诺了未经授权的退款。",
    )
    first = audit.submit(project_id, reviewer_id, severe)
    assert audit.submit(project_id, reviewer_id, severe).id == first.id
    assert db.query(ShadowDraftReview).count() == 1

    with pytest.raises(TGOAPIException) as conflict:
        audit.submit(
            project_id,
            reviewer_id,
            severe.model_copy(update={"verdict": "acceptable"}),
        )
    assert conflict.value.status_code == 409

    summary = audit.summary(project_id, days=7)
    assert summary.eligible_drafts == 1
    assert summary.evaluated_drafts == 1
    assert summary.ever_severe_drafts == 1
    assert summary.current_severe_drafts == 1
    assert summary.verified_eligible_drafts == 1
    assert summary.verified_evaluated_drafts == 1
    assert summary.verified_ever_severe_drafts == 1

    audit.submit(
        project_id,
        reviewer_id,
        ShadowReviewRequest(
            draft_id=draft.id,
            visitor_id=visitor_id,
            request_id=uuid4(),
            verdict="acceptable",
        ),
    )
    updated = audit.summary(project_id, days=7)
    assert updated.current_severe_drafts == 0
    assert updated.ever_severe_drafts == 1
    unverified = eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
    )
    audit.submit(
        project_id,
        reviewer_id,
        ShadowReviewRequest(
            draft_id=unverified.id,
            visitor_id=visitor_id,
            request_id=uuid4(),
            verdict="acceptable",
        ),
    )
    mixed = audit.summary(project_id, days=7)
    assert mixed.eligible_drafts == 2
    assert mixed.evaluated_drafts == 2
    assert mixed.verified_eligible_drafts == 1
    assert mixed.verified_evaluated_drafts == 1
    assert mixed.verified_ever_severe_drafts == 1
    audit.submit(
        project_id,
        reviewer_id,
        ShadowReviewRequest(
            draft_id=draft.id,
            visitor_id=visitor_id,
            request_id=uuid4(),
            verdict="insufficient_evidence",
        ),
    )
    revised = audit.summary(project_id, days=7)
    assert revised.verified_evaluated_drafts == 0
    assert revised.verified_ever_severe_drafts == 1
    latest = audit.latest_for_drafts(
        project_id, visitor_id, reviewer_id, {draft.id}
    )
    assert latest[draft.id].verdict == "insufficient_evidence"
    assert audit.latest_for_drafts(project_id, uuid4(), reviewer_id, {draft.id}) == {}
    assert audit.latest_for_drafts(project_id, visitor_id, uuid4(), {draft.id}) == {}


def test_review_rejects_unpaired_or_other_project_draft(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    draft = eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        status="unknown",
    )
    review = ShadowReviewRequest(
        draft_id=draft.id,
        visitor_id=visitor_id,
        request_id=uuid4(),
        verdict="acceptable",
    )
    with pytest.raises(TGOAPIException) as unpaired:
        ShadowReviewAudit(db).submit(project_id, uuid4(), review)
    assert unpaired.value.status_code == 409

    with pytest.raises(TGOAPIException) as foreign:
        ShadowReviewAudit(db).submit(uuid4(), uuid4(), review)
    assert foreign.value.status_code == 404

    active_draft = eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        mode="active",
    )
    with pytest.raises(TGOAPIException) as not_shadow:
        ShadowReviewAudit(db).submit(
            project_id,
            uuid4(),
            review.model_copy(
                update={"draft_id": active_draft.id, "request_id": uuid4()}
            ),
        )
    assert not_shadow.value.status_code == 409


def test_review_request_requires_reason_and_other_note():
    payload = {
        "draft_id": uuid4(),
        "visitor_id": uuid4(),
        "request_id": uuid4(),
        "verdict": "severe_error",
    }
    with pytest.raises(ValueError):
        ShadowReviewRequest(**payload)
    with pytest.raises(ValueError):
        ShadowReviewRequest(**payload, reason_code="other")
    accepted = ShadowReviewRequest(
        **payload,
        reason_code="other",
        note="  违反了退货政策  ",
    )
    assert accepted.note == "违反了退货政策"


def test_non_text_or_empty_draft_is_not_reviewable(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    image = eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        message_type=2,
    )
    empty = eligible_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        draft_text="   ",
    )
    audit = ShadowReviewAudit(db)
    for draft in (image, empty):
        with pytest.raises(TGOAPIException) as rejected:
            audit.submit(
                project_id,
                uuid4(),
                ShadowReviewRequest(
                    draft_id=draft.id,
                    visitor_id=visitor_id,
                    request_id=uuid4(),
                    verdict="acceptable",
                ),
            )
        assert rejected.value.status_code == 409
    assert audit.summary(project_id, days=7).eligible_drafts == 0

    db.add(
        StaffMessageDelivery(
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
            channel_id=f"{visitor_id}-vtr",
            channel_type=251,
            client_msg_no=f"staff-{uuid4()}",
            request_fingerprint="s" * 64,
            source_message_id=image.source_message_id,
            assist_draft_id=image.id,
            payload={"type": 1, "content": "这次是真正的文字回复"},
            external_status="sent",
            history_status="sent",
        )
    )
    db.commit()
    accepted = audit.submit(
        project_id,
        uuid4(),
        ShadowReviewRequest(
            draft_id=image.id,
            visitor_id=visitor_id,
            request_id=uuid4(),
            verdict="acceptable",
        ),
    )
    assert accepted.draft_id == image.id
    assert audit.summary(project_id, days=7).eligible_drafts == 1


def test_pending_queue_uses_verified_unreviewed_cases_and_stable_cursor(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    reviewer_id = uuid4()
    audit = ShadowReviewAudit(db)
    first = eligible_case(
        db, project_id=project_id, visitor_id=visitor_id,
        staff_id=staff_id, proof_status="verified",
    )
    second = eligible_case(
        db, project_id=project_id, visitor_id=visitor_id,
        staff_id=staff_id, proof_status="verified",
    )
    eligible_case(
        db, project_id=project_id, visitor_id=visitor_id,
        staff_id=staff_id,
    )
    eligible_case(
        db, project_id=project_id, visitor_id=visitor_id,
        staff_id=staff_id, status="unknown", proof_status="verified",
    )
    foreign = eligible_case(
        db, project_id=uuid4(), visitor_id=visitor_id,
        staff_id=staff_id, proof_status="verified",
    )

    page = audit.pending_queue(project_id, reviewer_id, days=7, limit=1)
    assert page.total_pending == 2
    assert [item.draft_id for item in page.items] == [first.id]
    assert page.next_cursor == first.id
    assert page.items[0].visitor_id == visitor_id
    assert "draft" not in page.items[0].model_dump()

    audit.submit(
        project_id, reviewer_id,
        ShadowReviewRequest(
            draft_id=first.id, visitor_id=visitor_id,
            request_id=uuid4(), verdict="acceptable",
        ),
    )
    later = audit.pending_queue(
        project_id, reviewer_id, days=7, limit=1, after_draft_id=first.id,
    )
    assert later.total_pending == 1
    assert [item.draft_id for item in later.items] == [second.id]
    assert later.next_cursor is None
    refreshed = audit.pending_queue(project_id, reviewer_id, days=7, limit=10)
    assert refreshed.items[0].draft_id == second.id
    another_reviewer = audit.pending_queue(project_id, uuid4(), days=7, limit=10)
    assert another_reviewer.total_pending == 2

    with pytest.raises(TGOAPIException) as invalid_cursor:
        audit.pending_queue(
            project_id, reviewer_id, days=7, limit=1, after_draft_id=foreign.id,
        )
    assert invalid_cursor.value.status_code == 404


def test_focus_draft_loads_older_case_without_exposing_other_project(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    older = eligible_case(
        db, project_id=project_id, visitor_id=visitor_id,
        staff_id=staff_id, proof_status="verified",
    )
    newer = eligible_case(
        db, project_id=project_id, visitor_id=visitor_id,
        staff_id=staff_id, proof_status="verified",
    )
    service = ShadowReviewService(db)
    recent = service.list_comparisons(project_id, visitor_id, uuid4(), 1)
    assert recent.items[0].source_message_id == newer.source_message_id

    focused = service.list_comparisons(
        project_id, visitor_id, uuid4(), 1, focus_draft_id=older.id,
    )
    assert len(focused.items) == 1
    assert focused.items[0].source_message_id == older.source_message_id
    assert [reply.ai_draft_id for reply in focused.items[0].replies] == [
        older.id
    ]

    with pytest.raises(TGOAPIException) as foreign:
        service.list_comparisons(
            uuid4(), visitor_id, uuid4(), 1, focus_draft_id=older.id,
        )
    assert foreign.value.status_code == 404


def test_pending_queue_cursor_handles_equal_creation_times(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    cases = [
        eligible_case(
            db, project_id=project_id, visitor_id=visitor_id,
            staff_id=staff_id, proof_status="verified",
        )
        for _ in range(2)
    ]
    shared_time = cases[0].created_at
    cases[1].created_at = shared_time
    db.commit()

    audit = ShadowReviewAudit(db)
    reviewer_id = uuid4()
    first = audit.pending_queue(project_id, reviewer_id, days=7, limit=1)
    second = audit.pending_queue(
        project_id, reviewer_id, days=7, limit=1,
        after_draft_id=first.next_cursor,
    )
    assert first.next_cursor is not None
    assert {first.items[0].draft_id, second.items[0].draft_id} == {
        case.id for case in cases
    }
    assert second.next_cursor is None
