"""Formal G4 reviewers cannot inspect conclusions before independent review."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Response

from app.api.v1.endpoints import message_analysis
from app.core.exceptions import TGOAPIException
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.schemas.shadow_review import ShadowReviewRequest
from app.schemas.shadow_sample import ShadowSampleCreateRequest
from app.services.shadow_blind_review_access import ShadowBlindReviewAccess
from app.services.shadow_review_audit import ShadowReviewAudit
from app.services.shadow_sample_service import ShadowSampleService
from tests.test_shadow_sample_service import add_case, db as shared_sample_db

db = shared_sample_db  # Re-export the existing SQLite fixture under its pytest name.


def frozen_protocol(db, project_id, visitor_id, staff_id, creator_id):
    draft = add_case(
        db, project_id=project_id, visitor_id=visitor_id, staff_id=staff_id
    )
    batch = ShadowSampleService(db).create_batch(
        project_id,
        creator_id,
        ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=1),
    )
    now = datetime.now(timezone.utc)
    db.add(
        ShadowReviewProtocol(
            project_id=project_id,
            creator_id=creator_id,
            request_id=uuid4(),
            request_fingerprint="a" * 64,
            window_start=batch.window_start,
            window_end=batch.window_end,
            sample_size=1,
            algorithm=batch.algorithm,
            random_seed=batch.random_seed,
            population_count=1,
            population_sha256=batch.population_sha256,
            manifest_sha256="b" * 64,
            batch_id=batch.id,
            created_at=now - timedelta(minutes=1),
            finalized_at=now,
        )
    )
    db.commit()
    return draft, batch


def test_official_results_wait_for_each_reviewers_own_assessment(db):
    project, visitor, staff, coordinator, reviewer = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    draft, _ = frozen_protocol(db, project, visitor, staff, coordinator)
    access = ShadowBlindReviewAccess(db)
    access.require_result_access(project, coordinator)
    access.require_result_access(uuid4(), reviewer)
    with pytest.raises(TGOAPIException) as locked:
        access.require_result_access(project, reviewer)
    assert locked.value.status_code == 403

    with pytest.raises(TGOAPIException) as conflict:
        ShadowReviewAudit(db).submit(
            project,
            coordinator,
            ShadowReviewRequest(
                draft_id=draft.id,
                visitor_id=visitor,
                request_id=uuid4(),
                verdict="acceptable",
            ),
        )
    assert conflict.value.status_code == 403

    review_request = ShadowReviewRequest(
        draft_id=draft.id,
        visitor_id=visitor,
        request_id=uuid4(),
        verdict="acceptable",
    )
    receipt = ShadowReviewAudit(db).submit(project, reviewer, review_request)
    assert ShadowReviewAudit(db).submit(project, reviewer, review_request) == receipt
    access.require_result_access(project, reviewer)
    with pytest.raises(TGOAPIException) as duplicate:
        ShadowReviewAudit(db).submit(
            project,
            reviewer,
            ShadowReviewRequest(
                draft_id=draft.id,
                visitor_id=visitor,
                request_id=uuid4(),
                verdict="minor_issue",
                reason_code="wrong_fact",
            ),
        )
    assert duplicate.value.status_code == 409
    with pytest.raises(TGOAPIException):
        access.require_result_access(project, uuid4())


def test_scheduled_protocol_hides_aggregate_results_until_frozen(db):
    project, coordinator, reviewer = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    db.add(
        ShadowReviewProtocol(
            project_id=project,
            creator_id=coordinator,
            request_id=uuid4(),
            request_fingerprint="c" * 64,
            window_start=now + timedelta(days=1),
            window_end=now + timedelta(days=2),
            sample_size=1,
            algorithm="sha256-seed-v1",
            random_seed="d" * 32,
            created_at=now,
        )
    )
    db.commit()
    ShadowBlindReviewAccess(db).require_result_access(project, coordinator)
    with pytest.raises(TGOAPIException) as locked:
        ShadowBlindReviewAccess(db).require_result_access(project, reviewer)
    assert locked.value.status_code == 403


def test_official_results_wait_for_every_selected_case(db):
    project, visitor, staff, coordinator, reviewer = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    drafts = [
        add_case(db, project_id=project, visitor_id=visitor, staff_id=staff)
        for _ in range(2)
    ]
    batch = ShadowSampleService(db).create_batch(
        project,
        coordinator,
        ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=2),
    )
    now = datetime.now(timezone.utc)
    db.add(
        ShadowReviewProtocol(
            project_id=project,
            creator_id=coordinator,
            request_id=uuid4(),
            request_fingerprint="a" * 64,
            window_start=batch.window_start,
            window_end=batch.window_end,
            sample_size=2,
            algorithm=batch.algorithm,
            random_seed=batch.random_seed,
            population_count=2,
            population_sha256=batch.population_sha256,
            manifest_sha256="b" * 64,
            batch_id=batch.id,
            created_at=now - timedelta(minutes=1),
            finalized_at=now,
        )
    )
    db.commit()
    audit = ShadowReviewAudit(db)
    access = ShadowBlindReviewAccess(db)
    for draft in drafts:
        with pytest.raises(TGOAPIException):
            access.require_result_access(project, reviewer)
        audit.submit(
            project,
            reviewer,
            ShadowReviewRequest(
                draft_id=draft.id,
                visitor_id=visitor,
                request_id=uuid4(),
                verdict="acceptable",
            ),
        )
    access.require_result_access(project, reviewer)


def test_result_endpoints_enforce_blind_review_lock(db, monkeypatch):
    project, visitor, staff, coordinator, reviewer = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    draft, batch = frozen_protocol(db, project, visitor, staff, coordinator)
    actor = SimpleNamespace(id=reviewer, project_id=project)
    from app.services import staff_conversation_scope

    monkeypatch.setattr(
        staff_conversation_scope, "require_owned_visitor", lambda *_args: None
    )
    requests = (
        lambda: message_analysis.get_staff_shadow_review_summary(
            response=Response(), days=7, current_user=actor, db=db
        ),
        lambda: message_analysis.get_staff_shadow_sample_batch(
            batch_id=batch.id, response=Response(), current_user=actor, db=db
        ),
        lambda: message_analysis.create_staff_shadow_sample_batch(
            request=ShadowSampleCreateRequest(
                request_id=uuid4(), days=7, sample_size=1
            ),
            response=Response(),
            current_user=actor,
            db=db,
        ),
        lambda: message_analysis.list_staff_shadow_adjudications(
            batch_id=batch.id,
            draft_id=draft.id,
            visitor_id=visitor,
            response=Response(),
            limit=20,
            before_id=None,
            current_user=actor,
            db=db,
        ),
    )
    for request in requests:
        with pytest.raises(TGOAPIException) as denied:
            request()
        assert denied.value.code == "SHADOW_BLIND_REVIEW_LOCKED"
