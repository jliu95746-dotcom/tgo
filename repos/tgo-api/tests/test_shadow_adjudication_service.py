"""A third administrator resolves only the exact disputed review snapshot."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.exceptions import TGOAPIException
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_review_adjudication import ShadowReviewAdjudication
from app.models.message_intent_result import MessageIntentResult
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_sample import ShadowSampleCreateRequest
from app.schemas.shadow_adjudication import ShadowAdjudicationRequest
from app.services.shadow_adjudication_service import ShadowAdjudicationService
from app.services.shadow_sample_service import ShadowSampleService
from tests.test_shadow_sample_service import add_case, db as shared_sample_db

db = shared_sample_db  # Re-export the shared pytest fixture under its declared name.


@pytest.fixture
def adjudication_db(db):
    yield db


def review(db, project_id, visitor_id, draft_id, reviewer_id, verdict):
    db.add(
        ShadowDraftReview(
            project_id=project_id,
            visitor_id=visitor_id,
            draft_id=draft_id,
            reviewer_id=reviewer_id,
            request_id=uuid4(),
            request_fingerprint="a" * 64,
            verdict=verdict,
            reason_code="wrong_fact" if verdict == "severe_error" else None,
            created_at=datetime.now(timezone.utc)
            + timedelta(seconds=db.query(ShadowDraftReview).count()),
        )
    )
    db.commit()


def disputed_case(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    draft = add_case(
        db, project_id=project_id, visitor_id=visitor_id, staff_id=staff_id
    )
    batch = ShadowSampleService(db).create_batch(
        project_id,
        uuid4(),
        ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=1),
    )
    reviewers = (uuid4(), uuid4())
    review(db, project_id, visitor_id, draft.id, reviewers[0], "acceptable")
    review(db, project_id, visitor_id, draft.id, reviewers[1], "minor_issue")
    state = ShadowSampleService(db).get_batch(project_id, batch.id)
    assert state.items[0].review_status == "disputed"
    assert state.items[0].review_set_sha256 is not None
    return project_id, visitor_id, staff_id, draft, batch, reviewers, state


def command(project_id, visitor_id, draft, batch, state):
    return ShadowAdjudicationRequest(
        request_id=uuid4(),
        batch_id=batch.id,
        draft_id=draft.id,
        visitor_id=visitor_id,
        expected_review_sha256=state.items[0].review_set_sha256,
        verdict="acceptable",
        note="已比对原消息、草稿和人工回复；属于表达差异。",
    )


def test_third_reviewer_can_resolve_exact_dispute_once(adjudication_db):
    db = adjudication_db
    project, visitor, _, draft, batch, reviewers, state = disputed_case(db)
    adjudicator = uuid4()
    service = ShadowAdjudicationService(db)
    request = command(project, visitor, draft, batch, state)

    receipt = service.submit(project, adjudicator, request)
    assert receipt.adjudicator_id == adjudicator
    assert receipt.review_set_sha256 == request.expected_review_sha256
    assert service.submit(project, adjudicator, request) == receipt
    assert db.query(ShadowReviewAdjudication).count() == 1

    current = ShadowSampleService(db).get_batch(project, batch.id)
    assert current.items[0].review_status == "adjudicated"
    assert current.items[0].adjudication_verdict == "acceptable"
    assert current.ready_count == 1
    assert current.resolved_count == 1
    assert current.disputed_count == 0

    with pytest.raises(TGOAPIException) as changed_request:
        service.submit(
            project,
            adjudicator,
            request.model_copy(update={"verdict": "minor_issue"}),
        )
    assert changed_request.value.status_code == 409
    for reviewer in reviewers:
        with pytest.raises(TGOAPIException) as self_review:
            service.submit(
                project,
                reviewer,
                request.model_copy(update={"request_id": uuid4()}),
            )
        assert self_review.value.status_code == 403


def test_changed_review_invalidates_adjudication_until_new_decision(adjudication_db):
    db = adjudication_db
    project, visitor, _, draft, batch, reviewers, state = disputed_case(db)
    service = ShadowAdjudicationService(db)
    request = command(project, visitor, draft, batch, state)
    service.submit(project, uuid4(), request)
    review(db, project, visitor, draft.id, reviewers[0], "minor_issue")
    no_conflict = ShadowSampleService(db).get_batch(project, batch.id)
    assert no_conflict.items[0].review_status == "agreed"
    assert no_conflict.items[0].adjudication_verdict is None
    review(db, project, visitor, draft.id, reviewers[0], "acceptable")
    changed = ShadowSampleService(db).get_batch(project, batch.id)
    assert changed.items[0].review_status == "disputed"
    assert changed.items[0].adjudication_verdict is None
    assert changed.items[0].review_set_sha256 != request.expected_review_sha256

    with pytest.raises(TGOAPIException) as stale:
        service.submit(
            project,
            uuid4(),
            request.model_copy(update={"request_id": uuid4()}),
        )
    assert stale.value.status_code == 409
    new_request = command(project, visitor, draft, batch, changed)
    service.submit(project, uuid4(), new_request)
    assert db.query(ShadowReviewAdjudication).count() == 2
    assert ShadowSampleService(db).get_batch(project, batch.id).resolved_count == 1


def test_historical_severe_flag_survives_adjudication(adjudication_db):
    db = adjudication_db
    project, visitor, _, draft, batch, _, state = disputed_case(db)
    review(db, project, visitor, draft.id, uuid4(), "severe_error")
    severe = ShadowSampleService(db).get_batch(project, batch.id)
    request = command(project, visitor, draft, batch, severe)
    ShadowAdjudicationService(db).submit(project, uuid4(), request)
    result = ShadowSampleService(db).get_batch(project, batch.id)
    assert result.items[0].review_status == "severe_flagged"
    assert result.items[0].adjudication_verdict == "acceptable"
    assert result.ever_severe_count == 1
    assert result.ready_count == 0


def test_severe_adjudication_blocks_readiness_even_without_severe_reviewer(
    adjudication_db,
):
    db = adjudication_db
    project, visitor, _, draft, batch, reviewers, state = disputed_case(db)
    request = command(project, visitor, draft, batch, state).model_copy(
        update={"verdict": "severe_error", "reason_code": "wrong_fact"}
    )
    ShadowAdjudicationService(db).submit(project, uuid4(), request)
    result = ShadowSampleService(db).get_batch(project, batch.id)
    assert result.items[0].review_status == "severe_flagged"
    assert result.items[0].adjudication_verdict == "severe_error"
    assert result.ever_severe_count == 1
    assert result.current_severe_count == 1
    assert result.severe_flagged_count == 1
    assert result.ready_count == 0

    review(db, project, visitor, draft.id, reviewers[0], "minor_issue")
    changed = ShadowSampleService(db).get_batch(project, batch.id)
    assert changed.items[0].adjudication_verdict is None
    assert changed.items[0].review_status == "severe_flagged"
    assert changed.ready_count == 0


def test_foreign_batch_and_original_staff_cannot_adjudicate(adjudication_db):
    db = adjudication_db
    project, visitor, staff, draft, batch, _, state = disputed_case(db)
    request = command(project, visitor, draft, batch, state)
    service = ShadowAdjudicationService(db)
    with pytest.raises(TGOAPIException) as foreign:
        service.submit(uuid4(), uuid4(), request)
    assert foreign.value.status_code == 404
    with pytest.raises(TGOAPIException) as author:
        service.submit(project, staff, request)
    assert author.value.status_code == 403
    assert db.query(ShadowReviewAdjudication).count() == 0


@pytest.mark.parametrize("missing_evidence", ["shadow_intent", "confirmed_delivery"])
def test_cannot_adjudicate_when_frozen_case_loses_live_evidence(
    adjudication_db, missing_evidence
):
    db = adjudication_db
    project, visitor, _, draft, batch, _, state = disputed_case(db)
    request = command(project, visitor, draft, batch, state)
    if missing_evidence == "shadow_intent":
        db.query(MessageIntentResult).filter_by(
            project_id=project,
            visitor_id=visitor,
            source_message_id=draft.source_message_id,
        ).delete()
    else:
        db.query(StaffMessageDelivery).filter_by(
            project_id=project,
            visitor_id=visitor,
            assist_draft_id=draft.id,
        ).delete()
    db.commit()

    with pytest.raises(TGOAPIException) as error:
        ShadowAdjudicationService(db).submit(project, uuid4(), request)
    assert error.value.status_code == 409
    assert db.query(ShadowReviewAdjudication).count() == 0


def test_adjudication_history_keeps_superseded_decisions_and_scopes_sample(
    adjudication_db,
):
    db = adjudication_db
    project, visitor, _, draft, batch, reviewers, state = disputed_case(db)
    service = ShadowAdjudicationService(db)
    first = service.submit(
        project, uuid4(), command(project, visitor, draft, batch, state)
    )
    review(db, project, visitor, draft.id, reviewers[0], "severe_error")
    revised = ShadowSampleService(db).get_batch(project, batch.id)
    second = service.submit(
        project,
        uuid4(),
        command(project, visitor, draft, batch, revised).model_copy(
            update={"verdict": "minor_issue", "reason_code": "wrong_fact"}
        ),
    )

    page = service.list_history(project, batch.id, draft.id, visitor, limit=1)
    assert page.items == (second,)
    assert page.has_more is True
    assert page.next_cursor == second.id
    older = service.list_history(
        project, batch.id, draft.id, visitor, limit=1, before_id=page.next_cursor
    )
    assert older.items == (first,)
    assert older.has_more is False
    assert older.next_cursor is None
    full = service.list_history(project, batch.id, draft.id, visitor, limit=10)
    assert full.items == (second, first)
    assert full.has_more is False

    with pytest.raises(TGOAPIException) as foreign_cursor:
        service.list_history(
            project, batch.id, draft.id, visitor, limit=10, before_id=uuid4()
        )
    assert foreign_cursor.value.status_code == 404

    for owner, case_visitor, case_draft in (
        (uuid4(), visitor, draft.id),
        (project, uuid4(), draft.id),
        (project, visitor, uuid4()),
    ):
        with pytest.raises(TGOAPIException) as missing:
            service.list_history(owner, batch.id, case_draft, case_visitor, limit=10)
        assert missing.value.status_code == 404
