"""Employee workflow runs retain only server supplied request lineage."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.api.executions import _create_execution_record
from app.config import settings
from app.database import get_db
from app.models.execution import WorkflowExecution, NodeExecution
from app.schemas.execution import WorkflowExecuteRequest, WorkflowExecutionOrigin
from app.services.workflow_lineage import require_parent_execution


def test_customer_origin_requires_employee_and_complete_identity():
    project_id, request_id, session_id = uuid4(), uuid4(), uuid4()
    origin = {
        "ai_request_id": "ai-run-a",
        "business_project_id": str(project_id),
        "business_request_id": str(request_id),
        "visitor_session_id": str(session_id),
    }
    with pytest.raises(ValidationError):
        WorkflowExecuteRequest(origin=origin)
    with pytest.raises(ValidationError):
        WorkflowExecutionOrigin.model_validate({
            "ai_request_id": "ai-run-a", "business_request_id": str(request_id),
        })
    accepted = WorkflowExecuteRequest(
        employee_agent_id=uuid4(), execution_mode="production",
        release_version=1, expected_release_id="release-a",
        expected_release_digest="a" * 64, origin=origin,
    )
    assert accepted.origin.business_request_id == request_id
    assert accepted.origin.visitor_session_id == session_id
    accepted_source = WorkflowExecutionOrigin.model_validate({
        **origin, "source_message_id": "customer-message",
        "source_channel": "web",
    })
    assert accepted_source.source_message_id == "customer-message"
    with pytest.raises(ValidationError):
        WorkflowExecutionOrigin.model_validate({
            **origin, "source_message_id": "customer-message",
        })
    with pytest.raises(ValidationError):
        WorkflowExecutionOrigin.model_validate({
            "ai_request_id": "ai-run-a", "source_message_id": "customer-message",
            "source_channel": "web",
        })


@pytest.mark.asyncio
async def test_record_persists_project_owned_request_lineage():
    project_id, request_id, session_id = uuid4(), uuid4(), uuid4()
    origin = WorkflowExecutionOrigin(
        ai_request_id="ai-run-a", business_project_id=project_id,
        business_request_id=request_id, visitor_session_id=session_id,
        source_message_id="customer-message", source_channel="web",
    )
    db = AsyncMock()
    db.add = Mock()
    row = await _create_execution_record(
        db, "workflow-a", str(project_id), {}, origin=origin,
        workflow_lineage=("workflow-a",),
    )
    assert row.ai_request_id == "ai-run-a"
    assert row.business_request_id == str(request_id)
    assert row.visitor_session_id == str(session_id)
    assert row.source_message_id == "customer-message"
    assert row.source_channel == "web"
    assert row.workflow_lineage == ["workflow-a"]
    with pytest.raises(ValueError, match="项目"):
        await _create_execution_record(db, "workflow-a", str(uuid4()), {}, origin=origin)
    assert db.add.call_count == 1


@pytest.mark.asyncio
async def test_parent_run_is_verified_against_persisted_project_and_chain():
    project_id, request_id, session_id, parent_id = uuid4(), uuid4(), uuid4(), uuid4()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('workflow-a')"))
            db.add(WorkflowExecution(
                id=str(parent_id), project_id=str(project_id),
                workflow_id="workflow-a", status="running", input={},
                workflow_lineage=["workflow-a"],
                business_request_id=str(request_id),
                visitor_session_id=str(session_id),
                source_message_id="original-message", source_channel="web",
            ))
            await db.commit()
            origin = WorkflowExecutionOrigin(
                ai_request_id="nested-ai", parent_execution_id=parent_id,
                workflow_lineage=("workflow-a",),
            )
            accepted = await require_parent_execution(db, origin, str(project_id))
            assert accepted.business_request_id == request_id
            assert accepted.visitor_session_id == session_id
            assert accepted.source_message_id == "original-message"
            assert accepted.source_channel == "web"
            with pytest.raises(PermissionError, match="原消息"):
                await require_parent_execution(db, origin.model_copy(update={
                    "source_message_id": "different-message",
                    "source_channel": "web",
                }), str(project_id))
            with pytest.raises(PermissionError):
                await require_parent_execution(db, origin, str(uuid4()))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_business_request_lookup_returns_only_current_project_runs(monkeypatch):
    project_id, other_project, request_id = uuid4(), uuid4(), uuid4()
    session_id, other_session_id = uuid4(), uuid4()
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("test-shared-secret"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('workflow-a')"))
            for run_id, owner, request, session in (
                ("owned-run", project_id, request_id, session_id),
                ("other-run", other_project, request_id, session_id),
                ("same-request-other-session", project_id, request_id, other_session_id),
                ("different-session", project_id, uuid4(), other_session_id),
                ("unverified-run", project_id, None, session_id),
            ):
                db.add(WorkflowExecution(
                    id=run_id, project_id=str(owner), workflow_id="workflow-a",
                    status="completed", input={"email": "private@example.test"},
                    business_request_id=str(request) if request is not None else None,
                    visitor_session_id=str(session),
                ))
            await db.commit()
        app = FastAPI()
        app.include_router(executions.router, prefix="/v1/workflows")

        async def test_session():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = test_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.get(
                f"/v1/workflows/executions/by-business-request/{request_id}",
                params={"project_id": str(project_id)},
            )
            response = await client.get(
                f"/v1/workflows/executions/by-business-request/{request_id}",
                params={"project_id": str(project_id)},
                headers={"X-Internal-API-Key": "test-shared-secret"},
            )
            denied_session = await client.get(
                f"/v1/workflows/executions/by-visitor-session/{session_id}",
                params={"project_id": str(project_id)},
            )
            session_response = await client.get(
                f"/v1/workflows/executions/by-visitor-session/{session_id}",
                params={"project_id": str(project_id)},
                headers={"X-Internal-API-Key": "test-shared-secret"},
            )
            exact_response = await client.get(
                f"/v1/workflows/executions/by-business-request/{request_id}",
                params={"project_id": str(project_id), "visitor_session_id": str(session_id)},
                headers={"X-Internal-API-Key": "test-shared-secret"},
            )
        assert denied.status_code == 403
        assert denied_session.status_code == 403
        assert response.status_code == 200
        assert {row["id"] for row in response.json()} == {
            "owned-run", "same-request-other-session",
        }
        assert session_response.status_code == 200
        assert [row["id"] for row in session_response.json()] == ["owned-run"]
        assert exact_response.status_code == 200
        assert [row["id"] for row in exact_response.json()] == ["owned-run"]
        assert session_response.json()[0]["input"] == {
            "capture_status": "redacted", "field_count": 1,
        }
        assert "private@example.test" not in session_response.text
        assert response.json()[0]["input"] == {
            "capture_status": "redacted", "field_count": 1,
        }
        assert "private@example.test" not in response.text
    finally:
        await engine.dispose()
