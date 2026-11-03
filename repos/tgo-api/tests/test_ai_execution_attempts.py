"""A customer message keeps each actual AI execution, including retries."""

from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.ai_interaction_run import AIInteractionRun
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.ai_execution_tool_event import AIExecutionToolEvent
from app.models.ai_execution_business_query import AIExecutionBusinessQuery
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.api.v1.endpoints import ai_runs
from app.utils.encoding import build_visitor_channel_id
from app.services import ai_interaction_run_service as runs
from app.schemas.business_query_audit import BusinessQueryAuditList


@pytest.fixture
def attempt_store(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AIInteractionRun.__table__.create(engine)
    AIExecutionAttempt.__table__.create(engine)
    AIExecutionToolEvent.__table__.create(engine)
    AIExecutionBusinessQuery.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(runs, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _interaction(db: Session) -> AIInteractionRun:
    visitor_id = uuid4()
    run = AIInteractionRun(
        id=uuid4(), project_id=uuid4(), platform_id=uuid4(), visitor_id=visitor_id,
        channel_id=build_visitor_channel_id(visitor_id),
        channel_type=251, source_message_id="source-1",
        request_fingerprint="a" * 64, response_client_msg_no="reply-1",
        status="running",
    )
    db.add(run)
    db.commit()
    return run


def test_bind_is_scoped_idempotent_and_rejects_conflicting_execution(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id = uuid4()
        runs.record_ai_execution_attempt(
            run.id, run.project_id, "reply-1", execution_id,
        )
        runs.record_ai_execution_attempt(
            run.id, run.project_id, "reply-1", execution_id,
        )
        assert len(db.scalars(select(AIExecutionAttempt)).all()) == 1
        with pytest.raises(ValueError, match="execution"):
            runs.record_ai_execution_attempt(
                run.id, run.project_id, "reply-1", uuid4(),
            )
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_execution_attempt(
                run.id, uuid4(), "reply-1", uuid4(),
            )
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_execution_attempt(
                run.id, run.project_id, "other-reply", uuid4(),
            )


def test_retry_keeps_previous_attempt_and_finish_is_scoped(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        first, second = uuid4(), uuid4()
        runs.record_ai_execution_attempt(run.id, run.project_id, "reply-1", first)
        runs.mark_ai_interaction_finished(
            run.id, response_client_msg_no="reply-1", error_message="provider failed",
        )
        db.refresh(run)
        run.status = "running"
        run.response_client_msg_no = "reply-2"
        db.commit()
        runs.record_ai_execution_attempt(run.id, run.project_id, "reply-2", second)
        runs.mark_ai_interaction_finished(run.id, response_client_msg_no="reply-1")
        db.refresh(run)
        assert run.status == "running"
        runs.mark_ai_interaction_finished(run.id, response_client_msg_no="reply-2")
        attempts = db.scalars(select(AIExecutionAttempt).order_by(
            AIExecutionAttempt.response_client_msg_no,
        )).all()
        assert [(row.execution_id, row.status) for row in attempts] == [
            (first, "failed"), (second, "completed"),
        ]


@pytest.mark.parametrize(
    ("failure", "stored_code"),
    [
        ("vendor response includes customer-phone-13800000000", "ai_processing_failed"),
        ("ai_workflow_failed", "ai_workflow_failed"),
        ("ai_stream_cancelled", "ai_stream_cancelled"),
    ],
)
def test_finish_stores_only_known_failure_codes(
    attempt_store, failure: str, stored_code: str,
) -> None:
    with attempt_store() as db:
        run = _interaction(db)
        runs.mark_ai_interaction_finished(
            run.id, response_client_msg_no="reply-1", error_message=failure,
        )
        db.refresh(run)
        assert run.status == "failed"
        assert run.error_message == stored_code


def test_late_duplicate_finish_does_not_change_a_terminal_result(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id = uuid4()
        runs.record_ai_execution_attempt(
            run.id, run.project_id, run.response_client_msg_no, execution_id,
        )
        runs.mark_ai_interaction_finished(run.id, response_client_msg_no="reply-1")
        runs.mark_ai_interaction_finished(
            run.id, response_client_msg_no="reply-1", error_message="late failure",
        )
        db.refresh(run)
        attempt = db.get(AIExecutionAttempt, execution_id)
        assert run.status == "completed" and attempt is not None
        assert attempt.status == "completed"


@pytest.mark.asyncio
async def test_admin_can_find_attempts_only_for_the_owned_original_message(
    attempt_store,
):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id = uuid4()
        runs.record_ai_execution_attempt(
            run.id, run.project_id, run.response_client_msg_no, execution_id,
        )
        app = FastAPI()
        app.include_router(ai_runs.router, prefix="/v1/ai/runs")
        app.dependency_overrides[get_db] = lambda: db
        role = SimpleNamespace(
            project_id=run.project_id, role="admin", username="tester",
        )
        app.dependency_overrides[get_current_active_user] = lambda: role
        body = {
            "channel_id": run.channel_id,
            "source_message_id": run.source_message_id,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owned = await client.post(
                "/v1/ai/runs/attempts/lookup-channel-message", json=body,
            )
            assert owned.status_code == 200
            assert owned.json()[0]["execution_id"] == str(execution_id)
            assert "source_message_id" not in owned.text
            role.project_id = uuid4()
            foreign = await client.post(
                "/v1/ai/runs/attempts/lookup-channel-message", json=body,
            )
            assert foreign.status_code == 200 and foreign.json() == []
            role.project_id, role.role = run.project_id, "user"
            denied = await client.post(
                "/v1/ai/runs/attempts/lookup-channel-message", json=body,
            )
            assert denied.status_code == 403


def test_tool_events_keep_order_and_reject_changed_or_foreign_attempt(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id = uuid4()
        runs.record_ai_execution_attempt(
            run.id, run.project_id, run.response_client_msg_no, execution_id,
        )
        args = (run.id, run.project_id, "reply-1", execution_id)
        runs.record_ai_tool_event(*args, 1, "lookup_order", "call-1", "started")
        runs.record_ai_tool_event(*args, 2, "lookup_order", "call-1", "completed")
        runs.record_ai_tool_event(*args, 2, "lookup_order", "call-1", "completed")
        assert [(item.sequence_no, item.status) for item in db.scalars(
            select(AIExecutionToolEvent).order_by(AIExecutionToolEvent.sequence_no)
        )] == [(1, "started"), (2, "completed")]
        with pytest.raises(ValueError, match="event"):
            runs.record_ai_tool_event(*args, 2, "lookup_order", "call-1", "failed")
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_tool_event(
                run.id, uuid4(), "reply-1", execution_id,
                3, "lookup_order", "call-1", "completed",
            )
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_tool_event(
                run.id, run.project_id, "reply-1", uuid4(),
                3, "lookup_order", "call-1", "completed",
            )
        with pytest.raises(ValueError, match="Invalid"):
            runs.record_ai_tool_event(
                *args, 3, "lookup_order\nprivate data", "call-1", "completed",
            )
        runs.mark_ai_interaction_finished(run.id, response_client_msg_no="reply-1")
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_tool_event(*args, 3, "lookup_order", "call-1", "completed")
        assert not any(
            column.name in {"tool_input", "tool_output", "error", "payload"}
            for column in AIExecutionToolEvent.__table__.columns
        )


@pytest.mark.asyncio
async def test_admin_tool_event_lookup_is_scoped_to_project(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id = uuid4()
        runs.record_ai_execution_attempt(
            run.id, run.project_id, "reply-1", execution_id,
        )
        runs.record_ai_tool_event(
            run.id, run.project_id, "reply-1", execution_id,
            1, "lookup_order", "call-1", "failed",
        )
        app = FastAPI()
        app.include_router(ai_runs.router, prefix="/v1/ai/runs")
        app.dependency_overrides[get_db] = lambda: db
        role = SimpleNamespace(
            project_id=run.project_id, role="admin", username="tester",
        )
        app.dependency_overrides[get_current_active_user] = lambda: role
        path = f"/v1/ai/runs/attempts/{execution_id}/tool-events"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            owned = await client.get(path)
            assert owned.status_code == 200
            assert [(item["sequence_no"], item["status"]) for item in owned.json()] == [
                (1, "failed"),
            ]
            assert "tool_output" not in owned.text
            role.project_id = uuid4()
            foreign = await client.get(path)
            assert foreign.status_code == 200 and foreign.json() == []
            role.project_id, role.role = run.project_id, "user"
            denied = await client.get(path)
            assert denied.status_code == 403


def test_business_query_link_is_idempotent_scoped_and_payload_free(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id, query_id = uuid4(), uuid4()
        runs.record_ai_execution_attempt(run.id, run.project_id, "reply-1", execution_id)
        args = (run.id, run.project_id, "reply-1", execution_id, query_id,
                "membership_query", "passed")
        runs.record_ai_business_query(*args)
        runs.record_ai_business_query(*args)
        assert [(item.query_id, item.operation) for item in runs.list_ai_business_queries(
            db, run.project_id, execution_id, 20, 0,
        )] == [(query_id, "membership_query")]
        assert len(db.scalars(select(AIExecutionBusinessQuery)).all()) == 1
        assert not any(column.name in {"input", "output", "payload", "content", "order_no"}
                       for column in AIExecutionBusinessQuery.__table__.columns)
        with pytest.raises(ValueError, match="changed"):
            runs.record_ai_business_query(*args[:-2], "order_query", "passed")
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_business_query(run.id, uuid4(), "reply-1", execution_id,
                                          uuid4(), "membership_query", "passed")
        assert runs.list_ai_business_queries(db, uuid4(), execution_id, 20, 0) == []
        runs.mark_ai_interaction_finished(run.id, response_client_msg_no="reply-1")
        with pytest.raises(ValueError, match="scope"):
            runs.record_ai_business_query(run.id, run.project_id, "reply-1",
                                          execution_id, uuid4(), "membership_query", "passed")


@pytest.mark.asyncio
async def test_admin_business_query_lookup_is_scoped_to_project(attempt_store):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id, query_id = uuid4(), uuid4()
        runs.record_ai_execution_attempt(run.id, run.project_id, "reply-1", execution_id)
        runs.record_ai_business_query(run.id, run.project_id, "reply-1", execution_id,
                                      query_id, "customer_profile_query", "failed")
        app = FastAPI()
        app.include_router(ai_runs.router, prefix="/v1/ai/runs")
        app.dependency_overrides[get_db] = lambda: db
        role = SimpleNamespace(project_id=run.project_id, role="admin", username="tester")
        app.dependency_overrides[get_current_active_user] = lambda: role
        path = f"/v1/ai/runs/attempts/{execution_id}/business-queries"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            owned = await client.get(path)
            assert owned.status_code == 200
            assert owned.json()[0]["query_id"] == str(query_id)
            assert "customer" not in owned.text.replace("customer_profile_query", "")
            role.project_id = uuid4()
            foreign = await client.get(path)
            assert foreign.status_code == 200 and foreign.json() == []
            role.project_id, role.role = run.project_id, "user"
            denied = await client.get(path)
            assert denied.status_code == 403


@pytest.mark.asyncio
async def test_admin_audit_observations_use_stored_attempt_reply_for_retries(
    attempt_store, monkeypatch,
):
    with attempt_store() as db:
        run = _interaction(db)
        execution_id = uuid4()
        runs.record_ai_execution_attempt(run.id, run.project_id, "reply-1", execution_id)
        run.response_client_msg_no = "reply-2"
        db.commit()
        read = AsyncMock(return_value=BusinessQueryAuditList(events=[], total=0))
        monkeypatch.setattr(ai_runs, "read_business_message_audits", read, raising=False)
        app = FastAPI()
        app.include_router(ai_runs.router, prefix="/v1/ai/runs")
        app.dependency_overrides[get_db] = lambda: db
        role = SimpleNamespace(project_id=run.project_id, role="admin", username="tester")
        app.dependency_overrides[get_current_active_user] = lambda: role
        path = f"/v1/ai/runs/attempts/{execution_id}/business-audit-observations"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            owned = await client.get(path, params={"skip": 2})
            assert owned.status_code == 200
            assert owned.headers["cache-control"] == "no-store"
            assert "source-1" not in owned.text and "reply-1" not in owned.text
            read.assert_awaited_once_with(run.project_id, "source-1", "reply-1", 2, 20)
            role.project_id = uuid4()
            foreign = await client.get(path)
            assert foreign.status_code == 404
            assert read.await_count == 1
            role.project_id, role.role = run.project_id, "user"
            denied = await client.get(path)
            assert denied.status_code == 403
