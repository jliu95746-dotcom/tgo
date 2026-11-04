"""An official G4 protocol is declared before cases arrive and freezes once."""

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol,
    ShadowReviewProtocolCandidate,
)
from app.models.shadow_review_batch import ShadowReviewBatch, ShadowReviewBatchItem
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.message_intent_result import MessageIntentResult
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_sample import ShadowProtocolCreateRequest
from app.services.shadow_review_protocol_service import ShadowReviewProtocolService
from tests.test_shadow_sample_service import add_case


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
        ShadowReviewProtocolCandidate,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_protocol_is_future_only_unique_and_seed_is_committed(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)
    project_id, creator_id = uuid4(), uuid4()
    request = ShadowProtocolCreateRequest(
        request_id=uuid4(),
        window_start=now + timedelta(hours=1),
        window_end=now + timedelta(days=7, hours=1),
        sample_size=200,
    )
    service = ShadowReviewProtocolService(db)
    created = service.register(project_id, creator_id, request)
    assert created.status == "scheduled"
    assert created.batch_id is None
    assert (
        created.seed_sha256
        == hashlib.sha256(
            bytes.fromhex(db.query(ShadowReviewProtocol).one().random_seed)
        ).hexdigest()
    )
    assert "random_seed" not in created.model_dump()
    assert service.register(project_id, creator_id, request) == created
    assert db.query(ShadowReviewProtocol).count() == 1
    assert service.get(uuid4()) is None
    with pytest.raises(TGOAPIException) as duplicate:
        service.register(
            project_id,
            creator_id,
            request.model_copy(update={"request_id": uuid4()}),
        )
    assert duplicate.value.status_code == 409
    with pytest.raises(TGOAPIException) as past:
        service.register(
            uuid4(),
            creator_id,
            request.model_copy(
                update={"window_start": now, "window_end": now + timedelta(days=7)}
            ),
        )
    assert past.value.status_code == 409


def test_protocol_finalization_uses_registered_window_and_is_idempotent(
    db, monkeypatch
):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    current = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: current["now"])
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    service = ShadowReviewProtocolService(db)
    plan = service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=start,
            window_end=end,
            sample_size=5,
        ),
    )
    with pytest.raises(TGOAPIException) as early:
        service.finalize(project_id)
    assert early.value.status_code == 409

    eligible = add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        at=start + timedelta(minutes=10),
    )
    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        at=end + timedelta(minutes=1),
    )
    current["now"] = end + timedelta(minutes=2)
    frozen = service.finalize(project_id)
    assert frozen.status == "frozen"
    assert frozen.batch_id is not None
    assert frozen.seed_sha256 == plan.seed_sha256
    batch = db.query(ShadowReviewBatch).filter_by(id=frozen.batch_id).one()
    assert batch.project_id == project_id
    assert batch.window_start.replace(tzinfo=timezone.utc) == start
    assert batch.window_end.replace(tzinfo=timezone.utc) == end
    assert batch.population_count == 1
    assert hashlib.sha256(bytes.fromhex(batch.random_seed)).hexdigest() == (
        plan.seed_sha256
    )
    assert [item.draft_id for item in db.query(ShadowReviewBatchItem).all()] == [
        eligible.id
    ]
    assert service.finalize(project_id) == frozen


def test_empty_protocol_result_is_permanent(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    current = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: current["now"])
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    service = ShadowReviewProtocolService(db)
    service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=start,
            window_end=end,
            sample_size=5,
        ),
    )
    current["now"] = end + timedelta(minutes=1)
    empty = service.finalize(project_id)
    assert empty.status == "empty"
    assert empty.batch_id is None
    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        at=start + timedelta(minutes=10),
    )
    assert service.finalize(project_id) == empty
    next_start = current["now"] + timedelta(hours=1)
    next_cycle = service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=next_start,
            window_end=next_start + timedelta(hours=1),
            sample_size=5,
        ),
    )
    assert next_cycle.status == "scheduled"
    assert next_cycle.id != empty.id
    assert service.get(project_id) == next_cycle
    history = service.list(project_id, limit=10)
    assert [item.id for item in history.items] == [next_cycle.id, empty.id]
    assert history.has_more is False


def test_protocol_excludes_analysis_or_delivery_completed_after_window(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    current = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: current["now"])
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    service = ShadowReviewProtocolService(db)
    service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=start,
            window_end=end,
            sample_size=5,
        ),
    )
    late_analysis = add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        at=start + timedelta(minutes=10),
    )
    late_delivery = add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        at=start + timedelta(minutes=20),
    )
    db.query(MessageIntentResult).filter_by(
        source_message_id=late_analysis.source_message_id
    ).one().created_at = end + timedelta(seconds=1)
    db.query(StaffMessageDelivery).filter_by(
        assist_draft_id=late_delivery.id
    ).one().updated_at = end + timedelta(seconds=1)
    db.commit()
    current["now"] = end + timedelta(minutes=1)
    frozen = service.finalize(project_id)
    assert frozen.status == "empty"
    assert frozen.population_count == 0
    assert frozen.population_sha256 == hashlib.sha256(b"").hexdigest()


def test_official_manifest_preserves_full_population_after_late_insert(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    current = {"now": now}
    monkeypatch.setattr(module, "_utc_now", lambda: current["now"])
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    service = ShadowReviewProtocolService(db)
    plan = service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=start,
            window_end=end,
            sample_size=1,
        ),
    )
    eligible = [
        add_case(
            db,
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
            at=start + timedelta(minutes=minute),
        )
        for minute in (10, 20, 30)
    ]
    current["now"] = end + timedelta(minutes=1)
    frozen = service.finalize(project_id)
    assert frozen.population_count == 3
    manifest = db.query(ShadowReviewProtocolCandidate).all()
    assert {row.draft_id for row in manifest} == {row.id for row in eligible}
    assert all(row.protocol_id == plan.id for row in manifest)
    assert (
        frozen.population_sha256
        == hashlib.sha256(
            b"".join(row.id.bytes for row in sorted(eligible, key=lambda row: row.id))
        ).hexdigest()
    )

    first_page = service.list_candidates(project_id, plan.id, limit=2)
    assert len(first_page.items) == 2
    assert first_page.total_candidates == 3
    assert first_page.next_cursor is not None
    second_page = service.list_candidates(
        project_id, plan.id, limit=2, after_draft_id=first_page.next_cursor
    )
    assert len(second_page.items) == 1
    assert second_page.next_cursor is None
    assert (
        sum(
            item.selected_position is not None
            for item in (*first_page.items, *second_page.items)
        )
        == 1
    )
    assert all("content" not in item.model_dump() for item in first_page.items)

    add_case(
        db,
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        at=start + timedelta(minutes=40),
    )
    assert db.query(ShadowReviewProtocolCandidate).count() == 3
    assert service.list_candidates(project_id, plan.id, limit=10).total_candidates == 3

    db.delete(eligible[0])
    db.commit()
    assert {
        item.draft_id
        for item in service.list_candidates(project_id, plan.id, limit=10).items
    } == {row.id for row in eligible}

    with pytest.raises(TGOAPIException) as foreign:
        service.list_candidates(uuid4(), plan.id, limit=10)
    assert foreign.value.status_code == 404


def test_official_manifest_integrity_detects_candidate_and_sample_tampering(
    db, monkeypatch
):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    service = ShadowReviewProtocolService(db)
    protocol = service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=start,
            window_end=end,
            sample_size=1,
        ),
    )
    with pytest.raises(TGOAPIException) as not_ready:
        service.verify_integrity(project_id, protocol.id)
    assert not_ready.value.status_code == 409
    with pytest.raises(TGOAPIException) as foreign:
        service.verify_integrity(uuid4(), protocol.id)
    assert foreign.value.status_code == 404
    for minute in (10, 20, 30):
        add_case(
            db,
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
            at=start + timedelta(minutes=minute),
        )
    monkeypatch.setattr(module, "_utc_now", lambda: end + timedelta(minutes=1))
    service.finalize(project_id)
    clean = service.verify_integrity(project_id, protocol.id)
    assert clean.consistent is True
    assert clean.population_count == 3
    assert clean.manifest_sha256 == clean.recorded_manifest_sha256

    selected = db.query(ShadowReviewBatchItem).one()
    unselected = (
        db.query(ShadowReviewProtocolCandidate)
        .filter(ShadowReviewProtocolCandidate.draft_id != selected.draft_id)
        .first()
    )
    assert unselected is not None
    unselected.visitor_id = uuid4()
    db.commit()
    changed_mapping = service.verify_integrity(project_id, protocol.id)
    assert changed_mapping.consistent is False
    assert changed_mapping.population_valid is True
    assert changed_mapping.manifest_valid is False

    unselected.visitor_id = visitor_id
    db.commit()
    selected.visitor_id = uuid4()
    db.commit()
    changed_selection = service.verify_integrity(project_id, protocol.id)
    assert changed_selection.consistent is False
    assert changed_selection.sample_valid is False

    selected.visitor_id = visitor_id
    batch = db.query(ShadowReviewBatch).one()
    batch.requested_size = 2
    db.commit()
    changed_conditions = service.verify_integrity(project_id, protocol.id)
    assert changed_conditions.consistent is False
    assert changed_conditions.metadata_valid is False

    batch.requested_size = 1
    saved_protocol = db.query(ShadowReviewProtocol).one()
    recorded_mapping_hash = saved_protocol.manifest_sha256
    saved_protocol.manifest_sha256 = None
    db.commit()
    legacy_without_mapping_hash = service.verify_integrity(project_id, protocol.id)
    assert legacy_without_mapping_hash.consistent is False
    assert legacy_without_mapping_hash.manifest_valid is False

    saved_protocol.manifest_sha256 = recorded_mapping_hash
    db.delete(unselected)
    db.commit()
    missing_candidate = service.verify_integrity(project_id, protocol.id)
    assert missing_candidate.consistent is False
    assert missing_candidate.population_valid is False


def test_empty_official_manifest_integrity_is_verifiable(db, monkeypatch):
    from app.services import shadow_review_protocol_service as module

    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)
    project_id = uuid4()
    service = ShadowReviewProtocolService(db)
    protocol = service.register(
        project_id,
        uuid4(),
        ShadowProtocolCreateRequest(
            request_id=uuid4(),
            window_start=now + timedelta(hours=1),
            window_end=now + timedelta(hours=2),
            sample_size=2,
        ),
    )
    monkeypatch.setattr(module, "_utc_now", lambda: now + timedelta(hours=3))
    service.finalize(project_id)
    result = service.verify_integrity(project_id, protocol.id)
    assert result.consistent is True
    assert result.population_count == 0
    assert result.sample_valid is True
