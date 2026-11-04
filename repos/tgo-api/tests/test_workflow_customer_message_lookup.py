"""A customer message resolves to only its verified session's workflow runs."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.ai_workflows import router
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.ai_interaction_run import AIInteractionRun
from app.models.visitor_session import VisitorSession
from app.schemas.ai_workflows import WorkflowExecution
from app.services.workflow_client import workflow_client
from app.utils.encoding import build_visitor_channel_id
from tests.test_business_execution_context import context_fixture, setup


def test_original_message_resolves_only_in_owned_session(context_fixture):
    from app.services.workflow_trace import find_business_request_for_message

    _, db, run, conversation, _, _, _, _ = context_fixture
    assert find_business_request_for_message(
        db, run.project_id, conversation.id, run.source_message_id,
    ) == run.id
    assert find_business_request_for_message(
        db, uuid4(), conversation.id, run.source_message_id,
    ) is None
    assert find_business_request_for_message(
        db, run.project_id, uuid4(), run.source_message_id,
    ) is None
    assert find_business_request_for_message(
        db, run.project_id, conversation.id, "different-message",
    ) is None
    conversation.status = "closed"
    run.status = "completed"
    db.commit()
    assert find_business_request_for_message(
        db, run.project_id, conversation.id, run.source_message_id,
    ) == run.id
    run.platform_id = uuid4()
    db.commit()
    assert find_business_request_for_message(
        db, run.project_id, conversation.id, run.source_message_id,
    ) is None


@pytest.mark.asyncio
async def test_admin_message_lookup_uses_original_request_and_session(context_fixture, monkeypatch):
    _, db, run, conversation, _, _, _, _ = context_fixture
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=run.project_id, role="admin",
    )
    app.dependency_overrides[get_db] = lambda: db
    owned = WorkflowExecution.model_validate({
        "id": "owned-run", "workflow_id": "workflow-a", "status": "completed",
        "business_request_id": str(run.id),
        "visitor_session_id": str(conversation.id),
        "started_at": datetime.now(UTC),
    })
    wrong_session = owned.model_copy(update={
        "id": "wrong-session", "visitor_session_id": str(uuid4()),
    })
    lookup = AsyncMock(return_value=[owned, wrong_session])
    monkeypatch.setattr(workflow_client, "list_business_request_executions", lookup)
    body = {
        "visitor_session_id": str(conversation.id),
        "source_message_id": run.source_message_id,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/ai/workflows/executions/lookup-customer-message", json=body,
        )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["owned-run"]
    lookup.assert_awaited_once_with(
        str(run.id), str(run.project_id), limit=20, skip=0,
        visitor_session_id=str(conversation.id),
        before_started_at=None, before_id=None,
    )

    lookup.reset_mock()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=uuid4(), role="admin",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        foreign = await client.post(
            "/v1/ai/workflows/executions/lookup-customer-message", json=body,
        )
    assert foreign.status_code == 200
    assert foreign.json() == []
    lookup.assert_not_awaited()

    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=run.project_id, role="user", username="staff-a",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/v1/ai/workflows/executions/lookup-channel-message", json=body,
        )
    assert denied.status_code == 403
    lookup.assert_not_awaited()

    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=run.project_id, role="user", username="staff-a",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/v1/ai/workflows/executions/lookup-customer-message", json=body,
        )
    assert denied.status_code == 403
    lookup.assert_not_awaited()

    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=run.project_id, role="admin",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post(
            "/v1/ai/workflows/executions/lookup-customer-message",
            json={**body, "source_message_id": "x" * 256},
        )
        injected = await client.post(
            "/v1/ai/workflows/executions/lookup-customer-message",
            json={**body, "project_id": str(uuid4())},
        )
    assert invalid.status_code == 422
    assert injected.status_code == 422
    lookup.assert_not_awaited()


def test_channel_message_resolves_only_one_saved_request(context_fixture):
    from app.services.workflow_trace import find_ai_request_for_channel_message

    _, db, run, _, _, _, _, _ = context_fixture
    assert find_ai_request_for_channel_message(
        db, run.project_id, run.channel_id, run.source_message_id,
    ).id == run.id
    assert find_ai_request_for_channel_message(
        db, uuid4(), run.channel_id, run.source_message_id,
    ) is None
    assert find_ai_request_for_channel_message(
        db, run.project_id, run.channel_id, "another-message",
    ) is None
    assert find_ai_request_for_channel_message(
        db, run.project_id, "not-a-visitor-channel", run.source_message_id,
    ) is None

    second = AIInteractionRun(
        id=uuid4(), project_id=run.project_id, platform_id=uuid4(),
        visitor_id=run.visitor_id, channel_id=build_visitor_channel_id(run.visitor_id),
        channel_type=251, source_message_id=run.source_message_id,
        request_fingerprint="b" * 64, response_client_msg_no="ai_" + uuid4().hex,
        status="completed",
    )
    db.add(second)
    db.commit()
    assert find_ai_request_for_channel_message(
        db, run.project_id, run.channel_id, run.source_message_id,
    ) is None


@pytest.mark.asyncio
async def test_channel_message_lookup_checks_saved_session_ownership(context_fixture, monkeypatch):
    _, db, run, conversation, _, _, _, _ = context_fixture
    other_visitor_session = VisitorSession(
        id=uuid4(), project_id=run.project_id, platform_id=run.platform_id,
        visitor_id=uuid4(), staff_id=uuid4(), status="closed",
    )
    db.add(other_visitor_session)
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=run.project_id, role="admin",
    )
    app.dependency_overrides[get_db] = lambda: db
    owned = WorkflowExecution.model_validate({
        "id": "owned-run", "workflow_id": "workflow-a", "status": "completed",
        "business_request_id": str(run.id), "visitor_session_id": str(conversation.id),
        "started_at": datetime.now(UTC),
    })
    wrong_session = owned.model_copy(update={
        "id": "wrong-session", "visitor_session_id": str(other_visitor_session.id),
    })
    wrong_request = owned.model_copy(update={
        "id": "wrong-request", "business_request_id": str(uuid4()),
    })
    lookup = AsyncMock(return_value=[owned, wrong_session, wrong_request])
    monkeypatch.setattr(workflow_client, "list_business_request_executions", lookup)
    body = {"channel_id": run.channel_id, "source_message_id": run.source_message_id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/ai/workflows/executions/lookup-channel-message", json=body,
        )
        injected = await client.post(
            "/v1/ai/workflows/executions/lookup-channel-message",
            json={**body, "project_id": str(uuid4())},
        )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["owned-run"]
    lookup.assert_awaited_once_with(str(run.id), str(run.project_id), limit=20, skip=0)
    assert injected.status_code == 422

    lookup.reset_mock()
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=uuid4(), role="admin",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        foreign = await client.post(
            "/v1/ai/workflows/executions/lookup-channel-message", json=body,
        )
    assert foreign.status_code == 200
    assert foreign.json() == []
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_message_pagination_counts_only_verified_records(context_fixture):
    from app.services.workflow_trace import page_workflow_records_for_ai_request

    _, db, run, conversation, _, _, _, _ = context_fixture
    owned = WorkflowExecution.model_validate({
        "id": "owned-1", "workflow_id": "workflow-a", "status": "completed",
        "business_request_id": str(run.id), "visitor_session_id": str(conversation.id),
        "started_at": datetime.now(UTC),
    })
    foreign = owned.model_copy(update={
        "id": "foreign", "visitor_session_id": str(uuid4()),
    })
    raw = [foreign, owned, foreign.model_copy(update={"id": "foreign-2"}),
           owned.model_copy(update={"id": "owned-2"}),
           owned.model_copy(update={"id": "owned-3"})]
    fetch = AsyncMock(side_effect=lambda skip, limit: raw[skip:skip + limit])
    page = await page_workflow_records_for_ai_request(db, run, 2, 1, fetch)
    assert [record.id for record in page] == ["owned-2", "owned-3"]
    assert [call.args for call in fetch.await_args_list] == [(0, 2), (2, 2), (4, 2)]


@pytest.mark.asyncio
async def test_channel_message_anchor_skips_newer_runs_and_filters_foreign_session(
    context_fixture, monkeypatch,
):
    _, db, run, conversation, _, _, _, _ = context_fixture
    started_at = datetime(2026, 10, 14, tzinfo=UTC)
    def make_run(run_id, session_id):
        return WorkflowExecution.model_validate({
            "id": run_id, "workflow_id": "workflow-a", "status": "completed",
            "business_request_id": str(run.id), "visitor_session_id": str(session_id),
            "started_at": started_at,
        })
    raw = [make_run("run-d", conversation.id),
           make_run("run-c", conversation.id),
           make_run("run-b", uuid4()),
           make_run("run-a", conversation.id)]

    async def lookup(_request_id, _project_id, *, limit, skip=0,
                     before_started_at=None, before_id=None):
        if before_id is None:
            return raw[skip:skip + limit]
        return [row for row in raw if row.id < before_id][:limit]

    monkeypatch.setattr(workflow_client, "list_business_request_executions", lookup)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=run.project_id, role="admin",
    )
    app.dependency_overrides[get_db] = lambda: db
    path = "/v1/ai/workflows/executions/lookup-channel-message"
    body = {"channel_id": run.channel_id, "source_message_id": run.source_message_id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(path, params={"limit": 2}, json=body)
        assert [row["id"] for row in first.json()] == ["run-d", "run-c"]
        raw.insert(0, make_run("run-new", conversation.id))
        second = await client.post(path, params={
            "limit": 2, "before_started_at": first.json()[-1]["started_at"],
            "before_id": first.json()[-1]["id"],
        }, json=body)
    assert second.status_code == 200
    assert [row["id"] for row in second.json()] == ["run-a"]
