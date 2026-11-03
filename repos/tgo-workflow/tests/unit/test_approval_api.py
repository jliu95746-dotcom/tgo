"""Approval API needs both the internal key and the assigned staff identity."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import approvals
from app.database import get_db
from app.main import app
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.execution_snapshot import definition_digest
from app.services.workflow_dispatch import WorkflowResumeDispatchError


@pytest.mark.asyncio
async def test_only_assignee_can_list_and_decide_and_approval_dispatches(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, other_project, workflow_id, assignee, stranger = (
        str(uuid4()) for _ in range(5)
    )
    execution_id, approval_id = str(uuid4()), str(uuid4())
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
    async with sessions() as db:
        await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow_id})
        db.add(WorkflowExecution(
            id=execution_id, project_id=project_id, workflow_id=workflow_id,
            status="waiting_approval", input={}, definition_snapshot={"nodes": []},
            definition_digest=definition_digest({"nodes": []}),
            active_approval_id=approval_id, started_at=datetime.now(UTC),
        ))
        db.add(WorkflowApprovalRequest(
            id=approval_id, project_id=project_id, execution_id=execution_id,
            node_id="approval", approver_staff_id=assignee,
            summary="请确认售后登记", checkpoint={}, status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        await db.commit()

    async def session_override():
        async with sessions() as db:
            yield db

    dispatched = []

    async def record_dispatch(run, request):
        dispatched.append((run.id, request.id))

    app.dependency_overrides[get_db] = session_override
    monkeypatch.setattr(approvals.settings, "SECRET_KEY", SecretStr("internal-test-key"))
    monkeypatch.setattr(approvals, "enqueue_approved_resume", record_dispatch)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            path = "/v1/workflows/approvals"
            params = {"project_id": project_id, "actor_staff_id": assignee}
            headers = {"X-Internal-API-Key": "internal-test-key"}
            assert (await client.get(path, params=params)).status_code == 403
            assert (await client.get(
                path, params={**params, "project_id": other_project}, headers=headers,
            )).json() == []
            assert (await client.get(
                path, params={**params, "actor_staff_id": stranger}, headers=headers,
            )).json() == []
            rows = (await client.get(path, params=params, headers=headers)).json()
            assert len(rows) == 1
            assert rows[0]["summary"] == "请确认售后登记"
            preview_digest = rows[0]["preview_digest"]
            assert len(preview_digest) == 64
            assert "checkpoint" not in rows[0]

            missing_preview = await client.post(
                f"{path}/{approval_id}/decision", params=params,
                headers=headers, json={"decision": "approve"},
            )
            assert missing_preview.status_code == 422
            stale_preview = await client.post(
                f"{path}/{approval_id}/decision", params=params,
                headers=headers, json={"decision": "approve", "preview_digest": "0" * 64},
            )
            assert stale_preview.status_code == 409
            response = await client.post(
                f"{path}/{approval_id}/decision", params={**params, "actor_staff_id": stranger},
                headers=headers, json={"decision": "approve", "preview_digest": preview_digest},
            )
            assert response.status_code == 409
            response = await client.post(
                f"{path}/{approval_id}/decision", params=params,
                headers=headers, json={"decision": "approve", "preview_digest": preview_digest},
            )
            assert response.status_code == 200
            assert response.json() == {
                "id": approval_id, "execution_id": execution_id,
                "status": "approved", "dispatch_status": "queued",
            }
            assert dispatched == [(execution_id, approval_id)]
            async with sessions() as db:
                approval = await db.get(WorkflowApprovalRequest, approval_id)
                assert approval.resume_dispatch_attempts == 1
            retry_rows = (await client.get(path, params=params, headers=headers)).json()
            assert retry_rows[0]["status"] == "approved"
            stale_retry = await client.post(
                f"{path}/{approval_id}/decision", params=params,
                headers=headers,
                json={"decision": "approve", "preview_digest": "0" * 64},
            )
            assert stale_retry.status_code == 409
            assert dispatched == [(execution_id, approval_id)]

            async def uncertain_dispatch(run, request):
                raise WorkflowResumeDispatchError("broker timeout")

            monkeypatch.setattr(approvals, "enqueue_approved_resume", uncertain_dispatch)
            repeat = await client.post(
                f"{path}/{approval_id}/decision", params=params,
                headers=headers, json={"decision": "approve", "preview_digest": preview_digest},
            )
            assert repeat.status_code == 200
            assert repeat.json()["dispatch_status"] == "unknown"
            async with sessions() as db:
                run = await db.get(WorkflowExecution, execution_id)
                assert run.status == "pending_resume"
                approval = await db.get(WorkflowApprovalRequest, approval_id)
                assert approval.resume_dispatch_attempts == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_aftersales_approval_customer_session_requires_current_assignee_and_action(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, other_project, workflow_id, assignee, stranger = (
        str(uuid4()) for _ in range(5)
    )
    execution_id, approval_id, session_id = (str(uuid4()) for _ in range(3))
    definition = {"nodes": [{
        "id": "approval", "type": "approval",
        "data": {"after_approval_action": "create_aftersales_review"},
    }]}
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
    async with sessions() as db:
        await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow_id})
        db.add(WorkflowExecution(
            id=execution_id, project_id=project_id, workflow_id=workflow_id,
            status="waiting_approval", input={}, definition_snapshot=definition,
            definition_digest=definition_digest(definition),
            execution_mode="production", employee_agent_id=str(uuid4()),
            business_request_id=str(uuid4()), visitor_session_id=session_id,
            source_message_id="customer-message-1", source_channel="web",
            active_approval_id=approval_id, started_at=datetime.now(UTC),
        ))
        db.add(WorkflowApprovalRequest(
            id=approval_id, project_id=project_id, execution_id=execution_id,
            node_id="approval", approver_staff_id=assignee,
            summary="请核对客户诉求", checkpoint={}, status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        await db.commit()

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session_override
    monkeypatch.setattr(approvals.settings, "SECRET_KEY", SecretStr("internal-test-key"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            path = f"/v1/workflows/approvals/{approval_id}/customer-session"
            params = {"project_id": project_id, "actor_staff_id": assignee}
            headers = {"X-Internal-API-Key": "internal-test-key"}
            assert (await client.get(path, params=params)).status_code == 403
            assert (await client.get(
                path, params={**params, "project_id": other_project}, headers=headers,
            )).status_code == 404
            assert (await client.get(
                path, params={**params, "actor_staff_id": stranger}, headers=headers,
            )).status_code == 404
            response = await client.get(path, params=params, headers=headers)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json() == {
                "visitor_session_id": session_id,
                "source_message_id": "customer-message-1",
                "source_channel": "web",
            }
            listed = await client.get("/v1/workflows/approvals", params=params, headers=headers)
            assert listed.json()[0]["customer_session_available"] is True

            async with sessions() as db:
                request = await db.get(WorkflowApprovalRequest, approval_id)
                request.expires_at = datetime.now(UTC) - timedelta(minutes=1)
                await db.commit()
            assert (await client.get(path, params=params, headers=headers)).status_code == 404
            async with sessions() as db:
                request = await db.get(WorkflowApprovalRequest, approval_id)
                request.expires_at = datetime.now(UTC) + timedelta(hours=1)
                await db.commit()

            async with sessions() as db:
                run = await db.get(WorkflowExecution, execution_id)
                run.definition_snapshot = {"nodes": [{
                    "id": "approval", "type": "approval", "data": {},
                }]}
                run.definition_digest = definition_digest(run.definition_snapshot)
                await db.commit()
            assert (await client.get(path, params=params, headers=headers)).status_code == 404
            listed = await client.get("/v1/workflows/approvals", params=params, headers=headers)
            assert listed.json()[0]["customer_session_available"] is False
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
