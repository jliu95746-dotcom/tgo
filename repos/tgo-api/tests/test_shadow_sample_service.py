"""Frozen G4 sample membership stays stable across reviews and retries."""

import hashlib
from datetime import datetime, timedelta, timezone
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
from app.models.shadow_review_adjudication import ShadowReviewAdjudication
from app.models.shadow_review_batch import (
    ShadowReviewBatch,
    ShadowReviewBatchItem,
)
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_sample import ShadowSampleCreateRequest
from app.services.shadow_sample_service import ShadowSampleService


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
        ShadowReviewAdjudication,
        ShadowReviewProtocol,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_case(
    db: Session,
    *,
    project_id,
    visitor_id,
    staff_id,
    platform_id=None,
    source_verified: bool = True,
    delivery_status: str = "sent",
    mode: str = "shadow",
    at: datetime | None = None,
) -> AssistDraftRecord:
    now = at or datetime.now(timezone.utc)
    source_id = f"source-{uuid4()}"
    draft = AssistDraftRecord(
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        request_id=uuid4(),
        request_fingerprint="a" * 64,
        source_message_id=source_id,
        response_data={"draft": "AI 草稿"},
        source_proof={"status": "verified" if source_verified else "missing"},
        created_at=now,
    )
    intent = MessageIntentResult(
        project_id=project_id,
        platform_id=platform_id or uuid4(),
        visitor_id=visitor_id,
        source_message_id=source_id,
        analysis_mode=mode,
        intent="product_inquiry",
        confidence=0.9,
        entities={},
        risk_level="low",
        recommended_route="human_handoff",
        need_human=True,
        taxonomy_version="v1",
        routing_reason="manual",
        classification_source="rule",
        classifier_version="v1",
        policy_version="v1",
        input_fingerprint="b" * 64,
        created_at=now,
    )
    db.add_all([draft, intent])
    db.flush()
    db.add(
        StaffMessageDelivery(
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
            channel_id=f"{visitor_id}-vtr",
            channel_type=251,
            client_msg_no=f"staff-{uuid4()}",
            request_fingerprint="c" * 64,
            source_message_id=source_id,
            assist_draft_id=draft.id,
            payload={"type": 1, "content": "请先提供订单号"},
            external_status=delivery_status,
            history_status="sent",
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return draft


def test_batch_freezes_verified_population_and_is_idempotent(db):
    project_id, visitor_id, staff_id, reviewer_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    eligible = [
        add_case(
            db,
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
        )
        for _ in range(3)
    ]
    db.add(
        ShadowDraftReview(
            project_id=project_id,
            visitor_id=visitor_id,
            draft_id=eligible[0].id,
            reviewer_id=reviewer_id,
            request_id=uuid4(),
            request_fingerprint="d" * 64,
            verdict="acceptable",
        )
    )
    db.commit()
    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        source_verified=False,
    )
    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        delivery_status="unknown",
    )
    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        mode="active",
    )
    add_case(
        db,
        project_id=uuid4(),
        visitor_id=visitor_id,
        staff_id=staff_id,
    )
    service = ShadowSampleService(db)
    request = ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=2)
    created = service.create_batch(project_id, reviewer_id, request)
    assert created.population_count == 3
    assert created.sample_count == 2
    assert len(created.population_sha256) == 64
    assert len(created.random_seed) == 32
    assert set(item.draft_id for item in created.items).issubset(
        {draft.id for draft in eligible}
    )
    seed_bytes = bytes.fromhex(created.random_seed)
    ranked = sorted(
        eligible,
        key=lambda draft: hashlib.sha256(seed_bytes + draft.id.bytes).digest(),
    )
    assert [item.draft_id for item in created.items] == [
        draft.id for draft in ranked[:2]
    ]
    assert (
        created.population_sha256
        == hashlib.sha256(
            b"".join(
                draft.id.bytes for draft in sorted(eligible, key=lambda row: row.id)
            )
        ).hexdigest()
    )
    assert [item.position for item in created.items] == [1, 2]
    assert all("draft" not in item.model_dump() for item in created.items)
    assert service.create_batch(project_id, reviewer_id, request) == created
    assert service.get_batch(project_id, created.id) == created
    listed = service.list_batches(project_id, limit=10)
    assert [item.id for item in listed.items] == [created.id]
    assert listed.has_more is False
    assert service.list_batches(uuid4(), limit=10).items == ()
    assert db.query(ShadowReviewBatch).count() == 1
    assert db.query(ShadowReviewBatchItem).count() == 2

    with pytest.raises(TGOAPIException) as conflict:
        service.create_batch(
            project_id,
            reviewer_id,
            request.model_copy(update={"sample_size": 3}),
        )
    assert conflict.value.status_code == 409
    with pytest.raises(TGOAPIException) as foreign:
        service.get_batch(uuid4(), created.id)
    assert foreign.value.status_code == 404


def test_batch_membership_does_not_change_after_new_case(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    first = add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
    )
    service = ShadowSampleService(db)
    created = service.create_batch(
        project_id,
        uuid4(),
        ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=5),
    )
    assert [item.draft_id for item in created.items] == [first.id]
    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
    )
    assert service.get_batch(project_id, created.id) == created


def test_empty_population_is_not_reported_as_passing_sample(db):
    with pytest.raises(TGOAPIException) as empty:
        ShadowSampleService(db).create_batch(
            uuid4(),
            uuid4(),
            ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=20),
        )
    assert empty.value.status_code == 409
    assert db.query(ShadowReviewBatch).count() == 0


def test_batch_progress_tracks_review_history_without_changing_membership(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    draft = add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
    )
    service = ShadowSampleService(db)
    batch = service.create_batch(
        project_id,
        uuid4(),
        ShadowSampleCreateRequest(
            request_id=uuid4(),
            days=7,
            sample_size=1,
        ),
    )
    assert batch.unreviewed_count == 1
    assert batch.reviewed_count == 0
    assert batch.missing_drafts_count == 0

    reviewed_at = datetime.now(timezone.utc)
    for verdict, timestamp in (
        ("severe_error", reviewed_at),
        ("acceptable", reviewed_at + timedelta(seconds=1)),
    ):
        db.add(
            ShadowDraftReview(
                project_id=project_id,
                visitor_id=visitor_id,
                draft_id=draft.id,
                reviewer_id=uuid4(),
                request_id=uuid4(),
                request_fingerprint="e" * 64,
                verdict=verdict,
                reason_code="wrong_fact" if verdict == "severe_error" else None,
                created_at=timestamp,
            )
        )
    db.commit()
    refreshed = service.get_batch(project_id, batch.id)
    assert [
        (item.position, item.draft_id, item.visitor_id) for item in refreshed.items
    ] == [(item.position, item.draft_id, item.visitor_id) for item in batch.items]
    assert refreshed.population_sha256 == batch.population_sha256
    assert refreshed.reviewed_count == 1
    assert refreshed.unreviewed_count == 0
    assert refreshed.current_severe_count == 0
    assert refreshed.ever_severe_count == 1


def test_batch_requires_two_distinct_reviewers_and_flags_disagreement(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    draft = add_case(
        db, project_id=project_id, visitor_id=visitor_id, staff_id=staff_id
    )
    service = ShadowSampleService(db)
    batch = service.create_batch(
        project_id,
        uuid4(),
        ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=1),
    )
    assert batch.items[0].review_status == "unreviewed"
    assert batch.ready_count == 0
    assert batch.needs_second_count == 0

    first_reviewer = uuid4()

    def review(reviewer_id, verdict):
        db.add(
            ShadowDraftReview(
                project_id=project_id,
                visitor_id=visitor_id,
                draft_id=draft.id,
                reviewer_id=reviewer_id,
                request_id=uuid4(),
                request_fingerprint="f" * 64,
                verdict=verdict,
                reason_code="wrong_fact" if verdict == "severe_error" else None,
                created_at=datetime.now(timezone.utc)
                + timedelta(seconds=db.query(ShadowDraftReview).count()),
            )
        )
        db.commit()
        return service.get_batch(project_id, batch.id)

    one = review(first_reviewer, "acceptable")
    assert one.items[0].reviewer_count == 1
    assert one.items[0].review_status == "needs_second"
    assert one.needs_second_count == 1

    revised = review(first_reviewer, "minor_issue")
    assert revised.items[0].reviewer_count == 1
    assert revised.items[0].review_status == "needs_second"

    agreed = review(uuid4(), "minor_issue")
    assert agreed.items[0].reviewer_count == 2
    assert agreed.items[0].review_status == "agreed"
    assert agreed.ready_count == 1

    disputed = review(uuid4(), "acceptable")
    assert disputed.items[0].review_status == "disputed"
    assert disputed.disputed_count == 1
    assert disputed.ready_count == 0

    severe = review(uuid4(), "severe_error")
    assert severe.items[0].review_status == "severe_flagged"
    assert severe.severe_flagged_count == 1
    assert severe.ever_severe_count == 1


def test_batch_insufficient_evidence_blocks_two_person_agreement(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    draft = add_case(
        db, project_id=project_id, visitor_id=visitor_id, staff_id=staff_id
    )
    service = ShadowSampleService(db)
    batch = service.create_batch(
        project_id,
        uuid4(),
        ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=1),
    )
    for verdict in ("acceptable", "acceptable", "insufficient_evidence"):
        db.add(
            ShadowDraftReview(
                project_id=project_id,
                visitor_id=visitor_id,
                draft_id=draft.id,
                reviewer_id=uuid4(),
                request_id=uuid4(),
                request_fingerprint="e" * 64,
                verdict=verdict,
            )
        )
    db.commit()
    state = service.get_batch(project_id, batch.id)
    assert state.items[0].review_status == "insufficient"
    assert state.items[0].reviewer_count == 3
    assert state.ready_count == 0
    assert state.insufficient_case_count == 1
