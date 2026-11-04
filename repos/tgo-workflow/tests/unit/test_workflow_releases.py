"""Published graphs stay fixed across edits, concurrent requests and rollback."""

from uuid import uuid4
import json

import pytest
import pytest_asyncio
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.workflow_release import WorkflowPublishRequest, WorkflowRollbackRequest
from app.schemas.workflow import WorkflowUpdate
from app.services.workflow_service import WorkflowService
from app.services import workflow_releases as releases
from app.api import executions, workflows
from app.database import get_db
from app.models.execution import WorkflowExecution, NodeExecution
from app.models.approval import WorkflowApprovalRequest
from unittest.mock import AsyncMock
from app.integrations.ai_client import AIClient


def approval_definition(approver_id: str):
    return {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input", "input_variables": []}},
        {"id": "approval", "type": "approval", "data": {
            "reference_key": "approval", "approver_staff_id": approver_id,
            "summary": "核对只读结果", "expires_in_minutes": 60,
        }},
        {"id": "answer", "type": "answer", "data": {
            "reference_key": "answer", "output_type": "template", "output_template": "已确认",
        }},
    ], "edges": [
        {"source": "input", "target": "approval"},
        {"source": "approval", "target": "answer"},
    ]}


@pytest.mark.asyncio
async def test_unreviewed_api_write_cannot_be_published(database):
    db, workflow, _ = database
    workflow.definition = {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input"}},
        {"id": "write", "type": "api", "data": {
            "reference_key": "write", "method": "POST",
            "url": "https://example.test/returns", "body_type": "json",
        }},
        {"id": "answer", "type": "answer", "data": {
            "reference_key": "answer", "output_type": "template", "output_template": "已登记",
        }},
    ], "edges": [
        {"source": "input", "target": "write"},
        {"source": "write", "target": "answer"},
    ]}
    await db.commit()

    with pytest.raises(HTTPException) as denied:
        await releases.publish(
            db, workflow.id, "project-a",
            WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
            "staff-a",
        )
    assert denied.value.status_code == 400
    assert (await db.scalars(select(WorkflowRelease))).all() == []


@pytest.mark.asyncio
async def test_revoked_approver_blocks_publish_rollback_and_execution(database, monkeypatch):
    db, workflow, sessions = database
    approver_id = str(uuid4())
    workflow.definition = approval_definition(approver_id)
    await db.commit()
    check = AsyncMock(side_effect=PermissionError("staff disabled"))
    monkeypatch.setattr(releases, "verify_definition_approval_assignees", check)
    with pytest.raises(HTTPException) as denied:
        await releases.publish(db, workflow.id, "project-a",
            WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    assert denied.value.status_code == 409
    assert (await db.scalars(select(WorkflowRelease))).all() == []

    check.side_effect = None
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    await db.commit()
    check.side_effect = PermissionError("staff disabled")
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    path = f"/v1/workflows/{workflow.id}/execute?project_id=project-a"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(path, json={"async": True, "execution_mode": "draft_test"})
    assert denied.status_code == 409
    assert (await db.scalars(select(WorkflowExecution))).all() == []

    check.side_effect = None
    workflow.definition = definition("safe-v2")
    workflow.version = 2
    await db.commit()
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=1), "staff-a")
    await db.commit()
    check.side_effect = PermissionError("staff disabled")
    with pytest.raises(HTTPException) as denied:
        await releases.rollback(db, workflow.id, "project-a",
            WorkflowRollbackRequest(target_version=1, expected_publication_revision=2))
    assert denied.value.status_code == 409
    assert workflow.published_version == 2


@pytest.mark.asyncio
async def test_missing_project_tool_blocks_publish_before_release_write(database, monkeypatch):
    db, workflow, _ = database
    tool_id = str(uuid4())
    workflow.definition = {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input"}},
        {"id": "tool", "type": "tool", "data": {"reference_key": "tool", "tool_id": tool_id}},
        {"id": "answer", "type": "answer", "data": {"reference_key": "answer", "output_type": "variable", "output_variable": "tool.result"}},
    ], "edges": [{"source": "input", "target": "tool"}, {"source": "tool", "target": "answer"}]}
    await db.commit()
    check = AsyncMock(side_effect=PermissionError("foreign tool"))
    monkeypatch.setattr(AIClient, "verify_tool_available", check, raising=False)

    with pytest.raises(HTTPException) as denied:
        await releases.publish(db, workflow.id, "project-a",
            WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    assert denied.value.status_code == 409
    assert (await db.scalars(select(WorkflowRelease))).all() == []
    check.assert_awaited_once_with("project-a", tool_id)


@pytest.mark.asyncio
async def test_revoked_tool_blocks_production_before_execution_record(database, monkeypatch):
    db, workflow, sessions = database
    tool_id = str(uuid4())
    workflow.definition = {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input"}},
        {"id": "tool", "type": "tool", "data": {"reference_key": "tool", "tool_id": tool_id}},
        {"id": "answer", "type": "answer", "data": {"reference_key": "answer", "output_type": "variable", "output_variable": "tool.result"}},
    ], "edges": [{"source": "input", "target": "tool"}, {"source": "tool", "target": "answer"}]}
    await db.commit()
    check = AsyncMock()
    monkeypatch.setattr(AIClient, "verify_tool_available", check)
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    await db.commit()
    check.side_effect = PermissionError("tool revoked")

    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    path = f"/v1/workflows/{workflow.id}/execute?project_id=project-a"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(path, json={"execution_mode": "production"})
    assert denied.status_code == 409
    async with sessions() as observed:
        assert (await observed.scalars(select(WorkflowExecution))).all() == []
    assert check.await_count == 2


@pytest.mark.asyncio
async def test_revoked_tool_blocks_rollback_to_old_release(database, monkeypatch):
    db, workflow, _ = database
    tool_id = str(uuid4())
    workflow.definition = {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input"}},
        {"id": "tool", "type": "tool", "data": {"reference_key": "tool", "tool_id": tool_id}},
        {"id": "answer", "type": "answer", "data": {"reference_key": "answer", "output_type": "variable", "output_variable": "tool.result"}},
    ], "edges": [{"source": "input", "target": "tool"}, {"source": "tool", "target": "answer"}]}
    await db.commit()
    check = AsyncMock()
    monkeypatch.setattr(AIClient, "verify_tool_available", check)
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    workflow.definition = definition("safe-v2")
    workflow.version = 2
    await db.commit()
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=1), "staff-a")
    await db.commit()
    check.side_effect = PermissionError("tool revoked")

    with pytest.raises(HTTPException) as denied:
        await releases.rollback(db, workflow.id, "project-a",
            WorkflowRollbackRequest(target_version=1, expected_publication_revision=2))
    assert denied.value.status_code == 409
    await db.refresh(workflow)
    assert workflow.published_version == 2


@pytest.mark.asyncio
async def test_nested_employee_cycle_is_rejected_before_binding_or_write(database, monkeypatch):
    db, workflow, sessions = database
    await releases.publish(
        db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
        "staff-a",
    )
    fixed = await releases.integration_release(db, workflow.id, "project-a", 1)
    parent_id = str(uuid4())
    db.add(WorkflowExecution(
        id=parent_id, project_id="project-a", workflow_id=workflow.id,
        status="running", input={}, workflow_lineage=[workflow.id],
    ))
    await db.commit()
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr(executions.settings, "SECRET_KEY", SecretStr("synthetic-workflow-key"))
    check = AsyncMock()
    monkeypatch.setattr(executions.AIClient, "verify_employee_workflow_binding", check)
    payload = {
        "execution_mode": "production", "release_version": 1,
        "expected_release_id": fixed.release_id,
        "expected_release_digest": fixed.definition_digest,
        "employee_agent_id": str(uuid4()),
        "origin": {
            "ai_request_id": "nested-ai", "parent_execution_id": parent_id,
            "workflow_lineage": [workflow.id],
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            f"/v1/workflows/{workflow.id}/execute?project_id=project-a",
            json=payload,
            headers={"X-Internal-API-Key": "synthetic-workflow-key"},
        )
    assert response.status_code == 409
    check.assert_not_awaited()
    async with sessions() as observed:
        rows = (await observed.scalars(select(WorkflowExecution))).all()
        assert [row.id for row in rows] == [parent_id]


@pytest.mark.asyncio
async def test_employee_execution_rechecks_binding_before_accepting_record(database, monkeypatch):
    db, workflow, sessions = database
    await releases.publish(
        db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
        "staff-a",
    )
    fixed = await releases.integration_release(db, workflow.id, "project-a", 1)
    await db.commit()
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr(executions.settings, "SECRET_KEY", SecretStr("synthetic-workflow-key"))
    check = AsyncMock()
    monkeypatch.setattr(executions.AIClient, "verify_employee_workflow_binding", check)
    employee_id = str(uuid4())
    payload = {
        "execution_mode": "production", "release_version": 1,
        "expected_release_id": fixed.release_id,
        "expected_release_digest": fixed.definition_digest,
        "employee_agent_id": employee_id,
    }
    path = f"/v1/workflows/{workflow.id}/execute?project_id=project-a"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unsigned = await client.post(path, json=payload)
        assert unsigned.status_code == 403
        check.assert_not_awaited()

        unscoped = await client.post(
            path, json={key: value for key, value in payload.items()
                        if key != "employee_agent_id"},
            headers={"X-Internal-API-Key": "synthetic-workflow-key"},
        )
        assert unscoped.status_code == 422
        check.assert_not_awaited()

        check.side_effect = PermissionError("revoked")
        denied = await client.post(
            path, json=payload, headers={"X-Internal-API-Key": "synthetic-workflow-key"}
        )
        assert denied.status_code == 409
        async with sessions() as observed:
            assert (await observed.scalars(select(WorkflowExecution))).all() == []

        check.side_effect = RuntimeError("upstream offline")
        unavailable = await client.post(
            path, json=payload, headers={"X-Internal-API-Key": "synthetic-workflow-key"}
        )
        assert unavailable.status_code == 503
        async with sessions() as observed:
            assert (await observed.scalars(select(WorkflowExecution))).all() == []

        async def deactivate_while_checking(*_args):
            async with sessions() as changed:
                await changed.execute(
                    update(Workflow).where(Workflow.id == workflow.id).values(status="draft")
                )
                await changed.commit()

        check.side_effect = deactivate_while_checking
        deactivated = await client.post(
            path, json=payload, headers={"X-Internal-API-Key": "synthetic-workflow-key"}
        )
        assert deactivated.status_code == 409
        async with sessions() as observed:
            assert (await observed.scalars(select(WorkflowExecution))).all() == []
        async with sessions() as restored:
            await restored.execute(
                update(Workflow).where(Workflow.id == workflow.id).values(status="active")
            )
            await restored.commit()

        check.side_effect = None
        accepted = await client.post(
            path, json=payload, headers={"X-Internal-API-Key": "synthetic-workflow-key"}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["output"] == "v1"
        async with sessions() as observed:
            saved = (await observed.scalars(select(WorkflowExecution))).one()
            assert saved.employee_agent_id == employee_id
        check.assert_awaited_with(
            "project-a", employee_id, workflow.id, 1,
            fixed.release_id, fixed.definition_digest,
        )


def definition(answer):
    return {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input"}},
        {"id": "answer", "type": "answer", "data": {"reference_key": "answer", "output_type": "template", "output_template": answer}},
    ], "edges": [{"id": "edge", "source": "input", "target": "answer"}]}


@pytest_asyncio.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Workflow.__table__.create)
        await connection.run_sync(WorkflowRelease.__table__.create)
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        await connection.run_sync(NodeExecution.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        workflow = Workflow(id=str(uuid4()), project_id="project-a", name="订单查询", status="draft", definition=definition("v1"), tags=[], version=1)
        db.add(workflow)
        await db.commit()
        yield db, workflow, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_employee_approval_submission_is_idempotent(database, monkeypatch):
    db, workflow, sessions = database
    workflow.definition = approval_definition(str(uuid4()))
    await db.commit()
    monkeypatch.setattr(releases, "verify_definition_approval_assignees", AsyncMock())
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
        "staff-a")
    fixed = await releases.integration_release(db, workflow.id, "project-a", 1)
    await db.commit()

    monkeypatch.setattr(executions, "verify_approval_dependencies", AsyncMock())
    monkeypatch.setattr(executions.AIClient, "verify_employee_workflow_binding", AsyncMock())
    monkeypatch.setattr(executions.settings, "SECRET_KEY", SecretStr("synthetic-workflow-key"))
    enqueue = AsyncMock()
    monkeypatch.setattr(executions, "enqueue_workflow", enqueue)
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    payload = {
        "execution_mode": "production", "release_version": 1,
        "expected_release_id": fixed.release_id,
        "expected_release_digest": fixed.definition_digest,
        "employee_agent_id": str(uuid4()), "origin": {"ai_request_id": "run-1"},
        "async": True, "invocation_key": str(uuid4()), "inputs": {},
    }
    path = f"/v1/workflows/{workflow.id}/execute?project_id=project-a"
    headers = {"X-Internal-API-Key": "synthetic-workflow-key"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        missing = await client.post(path, json={key: value for key, value in payload.items()
                                                if key != "invocation_key"}, headers=headers)
        assert missing.status_code == 422
        first = await client.post(path, json=payload, headers=headers)
        assert first.status_code == 200, first.text
        repeated = await client.post(path, json=payload, headers=headers)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["id"] == first.json()["id"]
        async with sessions() as terminal_db:
            terminal = await terminal_db.get(WorkflowExecution, first.json()["id"])
            terminal.status = "completed"
            terminal.input = None
            await terminal_db.commit()
        terminal_retry = await client.post(path, json=payload, headers=headers)
        assert terminal_retry.status_code == 200, terminal_retry.text
        assert terminal_retry.json()["id"] == first.json()["id"]
        conflicting = await client.post(path, json={**payload, "inputs": {"question": "changed"}},
                                        headers=headers)
        assert conflicting.status_code == 409
        monkeypatch.setattr(
            executions.settings, "SECRET_KEY", SecretStr("rotated-workflow-key"),
        )
        rotated = await client.post(
            path, json=payload,
            headers={"X-Internal-API-Key": "rotated-workflow-key"},
        )
        assert rotated.status_code == 409
    enqueue.assert_awaited_once()
    async with sessions() as observed:
        rows = (await observed.scalars(select(WorkflowExecution))).all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_aftersales_release_reports_live_approval_action(database, monkeypatch):
    db, workflow, sessions = database
    graph = approval_definition(str(uuid4()))
    graph["nodes"][0]["data"]["input_variables"] = [
        {"name": "question", "type": "string", "required": True},
    ]
    graph["nodes"][1]["data"].update(
        after_approval_action="create_aftersales_review",
        case_category="return_request",
    )
    workflow.definition = graph
    await db.commit()
    check = AsyncMock()
    monkeypatch.setattr(releases, "verify_definition_approval_assignees", check)
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
        "staff-a")
    summary = await releases.integration_release(db, workflow.id, "project-a", 1)
    assert summary.approval_actions == ["create_aftersales_review"]
    assert check.await_count == 2
    app = FastAPI()
    app.include_router(workflows.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get(
            f"/v1/workflows/{workflow.id}/releases/1/integration",
            params={"project_id": "project-a"},
        )
    assert response.status_code == 200
    assert response.json()["approval_actions"] == ["create_aftersales_review"]
    assert check.await_count == 3
    check.side_effect = PermissionError("revoked")
    with pytest.raises(HTTPException) as denied:
        await releases.integration_release(db, workflow.id, "project-a", 1)
    assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_editing_a_published_workflow_keeps_the_production_graph(database):
    db, workflow, _ = database
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    first = await releases.production_definition(db, workflow.id, "project-a")
    workflow.definition = definition("new-draft")
    workflow.version += 1
    await db.commit()
    current = await releases.production_definition(db, workflow.id, "project-a")
    assert current.definition == first.definition == definition("v1")
    assert current.version == first.version == 1
    assert current.release_id == first.release_id
    assert workflow.definition == definition("new-draft")


@pytest.mark.asyncio
async def test_integration_release_keeps_original_input_contract_after_new_publish(database):
    db, workflow, _ = database
    workflow.definition = {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input", "input_variables": [
            {"name": "order_id", "type": "string", "required": True},
        ]}},
        {"id": "answer", "type": "answer", "data": {"reference_key": "answer", "output_type": "template", "output_template": "v1"}},
    ], "edges": [{"id": "edge", "source": "input", "target": "answer"}]}
    await db.commit()
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    fixed = await releases.integration_release(db, workflow.id, "project-a", 1)
    assert fixed.release_version == 1
    assert fixed.input_parameters[0].name == "order_id"
    assert len(fixed.definition_digest) == 64

    workflow.definition = definition("v2")
    workflow.version = 2
    await db.commit()
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=1), "staff-a")
    unchanged = await releases.integration_release(db, workflow.id, "project-a", 1)
    assert unchanged == fixed
    with pytest.raises(HTTPException) as foreign:
        await releases.integration_release(db, workflow.id, "project-b", 1)
    assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_integration_release_describes_governed_business_dependencies(database):
    db, workflow, sessions = database
    connection_id = str(uuid4())
    workflow.definition = {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "reference_key": "input", "input_variables": [
                    {"name": "order_no", "type": "string", "required": True},
                ],
            }},
            {"id": "query", "type": "business_query", "data": {
                "reference_key": "order", "connection_id": connection_id,
                "operation": "order_query", "order_variable": "input.order_no",
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "variable",
                "output_variable": "order.status",
            }},
        ],
        "edges": [
            {"source": "input", "target": "query"},
            {"source": "query", "target": "answer"},
        ],
    }
    await db.commit()
    await releases.publish(db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    await db.commit()

    fixed = await releases.integration_release(db, workflow.id, "project-a", 1)

    assert fixed.node_types == ["input", "business_query", "answer"]
    assert [(item.connection_id, item.operation) for item in fixed.required_business_queries] == [
        (connection_id, "order_query")
    ]
    app = FastAPI()
    app.include_router(workflows.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db

    app.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get(
            f"/v1/workflows/{workflow.id}/releases/1/integration",
            params={"project_id": "project-a"},
        )
    assert response.status_code == 200
    assert response.json()["node_types"] == fixed.node_types
    assert response.json()["required_business_queries"] == [
        {"connection_id": connection_id, "operation": "order_query"}
    ]


@pytest.mark.asyncio
async def test_stale_publish_and_missing_release_do_not_create_new_versions(database):
    db, workflow, _ = database
    with pytest.raises(HTTPException) as missing:
        await releases.production_definition(db, workflow.id, "project-a")
    assert missing.value.status_code == 409
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    for request in [WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), WorkflowPublishRequest(expected_draft_version=99, expected_publication_revision=1)]:
        with pytest.raises(HTTPException) as stale:
            await releases.publish(db, workflow.id, "project-a", request, "staff-a")
        assert stale.value.status_code == 409
    assert len((await db.scalars(select(WorkflowRelease))).all()) == 1


@pytest.mark.asyncio
async def test_rollback_switches_published_pointer_without_rewriting_the_draft(database):
    db, workflow, _ = database
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    workflow.definition = definition("v2")
    workflow.version = 2
    await db.commit()
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=1), "staff-a")
    await releases.rollback(db, workflow.id, "project-a", WorkflowRollbackRequest(target_version=1, expected_publication_revision=2))
    result = await releases.production_definition(db, workflow.id, "project-a")
    assert result.definition == definition("v1")
    await db.refresh(workflow)
    assert workflow.definition == definition("v2")
    assert workflow.version == 2
    assert workflow.publication_revision == 3
    assert len(await releases.history(db, workflow.id, "project-a")) == 2
    with pytest.raises(HTTPException) as duplicate:
        await releases.rollback(db, workflow.id, "project-a", WorkflowRollbackRequest(target_version=1, expected_publication_revision=2))
    assert duplicate.value.status_code == 409


@pytest.mark.asyncio
async def test_release_history_and_execution_are_tenant_scoped(database):
    db, workflow, _ = database
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    for operation in [releases.history(db, workflow.id, "project-b"), releases.production_definition(db, workflow.id, "project-b")]:
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_corrupt_release_never_falls_back_to_the_draft(database):
    db, workflow, _ = database
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    release = (await db.scalars(select(WorkflowRelease))).one()
    release.definition = definition("tampered")
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await releases.production_definition(db, workflow.id, "project-a")
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_draft_updates_use_revision_and_cannot_bypass_the_publish_endpoint(database):
    db, workflow, _ = database
    await WorkflowService.update(db, workflow.id, "project-a", WorkflowUpdate(name="修改名称", expected_version=1))
    assert workflow.version == 2
    with pytest.raises(HTTPException) as stale:
        await WorkflowService.update(db, workflow.id, "project-a", WorkflowUpdate(name="旧名称", expected_version=1))
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as bypass:
        await WorkflowService.update(db, workflow.id, "project-a", WorkflowUpdate(status="active", expected_version=2))
    assert bypass.value.status_code == 409


@pytest.mark.asyncio
async def test_fixed_release_can_be_read_after_new_release_but_archiving_stops_execution(database):
    db, workflow, _ = database
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    workflow.definition = definition("v2")
    workflow.version = 2
    await db.commit()
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=1), "staff-a")
    assert (await releases.production_definition(db, workflow.id, "project-a", 1)).definition == definition("v1")
    workflow.status = "archived"
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await releases.production_definition(db, workflow.id, "project-a", 1)
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"stream": True}, {"async": True}])
async def test_http_production_uses_release_and_draft_test_uses_current_edits(database, monkeypatch, mode):
    db, workflow, sessions = database
    await releases.publish(db, workflow.id, "project-a", WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0), "staff-a")
    workflow.definition = definition("new-draft")
    workflow.version = 2
    await db.commit()
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db
            await request_db.commit()

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr(executions, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(executions, "enqueue_workflow", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/v1/workflows/{workflow.id}/execute?project_id=project-a"
        response = await client.post(path, json={"execution_mode": "production", **mode})
        assert response.status_code == 200
        if mode.get("stream"):
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert events[-1]["event"] == "workflow_finished"
            assert events[-1]["data"]["status"] == "succeeded"
            assert events[-1]["data"]["outputs"] == "v1"
            assert "new-draft" not in response.text
        elif not mode.get("async"):
            assert response.json()["output"] == "v1"
        async with sessions() as observed:
            record = (await observed.scalars(select(WorkflowExecution))).one()
            assert record.workflow_version == 1
            assert record.release_id is not None
            assert record.definition_snapshot == definition("v1")
        draft = await client.post(path, json={"execution_mode": "draft_test"})
        assert draft.status_code == 200
        assert draft.json()["output"] == "new-draft"


@pytest.mark.asyncio
async def test_execution_rechecks_bound_release_before_creating_a_record(database):
    db, workflow, sessions = database
    await releases.publish(
        db, workflow.id, "project-a",
        WorkflowPublishRequest(expected_draft_version=1, expected_publication_revision=0),
        "staff-a",
    )
    fixed = await releases.integration_release(db, workflow.id, "project-a", 1)
    await db.commit()
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def session():
        async with sessions() as request_db:
            yield request_db
            await request_db.commit()

    app.dependency_overrides[get_db] = session
    path = f"/v1/workflows/{workflow.id}/execute?project_id=project-a"
    payload = {
        "execution_mode": "production", "release_version": 1,
        "expected_release_id": fixed.release_id,
        "expected_release_digest": fixed.definition_digest,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        replaced = await client.post(
            path, json={**payload, "expected_release_id": "another-release"}
        )
        assert replaced.status_code == 409
        changed = await client.post(
            path, json={**payload, "expected_release_digest": "b" * 64}
        )
        assert changed.status_code == 409
        partial = await client.post(
            path, json={**payload, "expected_release_digest": None}
        )
        assert partial.status_code == 422, partial.text
        async with sessions() as observed:
            assert (await observed.scalars(select(WorkflowExecution))).all() == []
        accepted = await client.post(path, json=payload)
        assert accepted.status_code == 200
        assert accepted.json()["output"] == "v1"
