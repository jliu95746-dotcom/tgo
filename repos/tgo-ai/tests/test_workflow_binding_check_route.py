"""The workflow receiver may confirm a current employee binding over a private hop."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_execution as endpoint
from app.config import settings
from app.database import get_db
from app.models.agent import Agent
from app.models.workflow import AgentWorkflow


@pytest.mark.asyncio
async def test_private_workflow_binding_check_rejects_wrong_identity_and_revocation(
    monkeypatch,
):
    project_id, agent_id, workflow_id = uuid4(), uuid4(), str(uuid4())
    reference = {
        "project_id": str(project_id),
        "agent_id": str(agent_id),
        "workflow_id": workflow_id,
        "release_version": 2,
        "release_id": str(uuid4()),
        "release_digest": "a" * 64,
    }
    monkeypatch.setattr(settings, "secret_key", "synthetic-private-workflow-key")
    check = AsyncMock()
    monkeypatch.setattr(endpoint, "require_current_binding", check, raising=False)
    application = FastAPI()
    application.include_router(endpoint.router, prefix="/api/v1/agents")
    fake_db = object()
    application.dependency_overrides[get_db] = lambda: fake_db
    path = "/api/v1/agents/workflow-binding-check"
    headers = {
        "X-Internal-API-Key": "synthetic-private-workflow-key",
        "X-Project-Id": str(project_id),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        for wrong in (
            {},
            {**headers, "X-Internal-API-Key": "wrong"},
            {**headers, "X-Project-Id": str(uuid4())},
        ):
            denied = await client.post(path, json=reference, headers=wrong)
            assert denied.status_code == 403
        check.assert_not_awaited()

        accepted = await client.post(path, json=reference, headers=headers)
        assert accepted.status_code == 200
        assert accepted.json() == {"authorized": True}
        check.assert_awaited_once_with(
            fake_db,
            str(agent_id),
            str(project_id),
            workflow_id,
            2,
            reference["release_id"],
            "a" * 64,
        )

        check.reset_mock(side_effect=True)
        check.side_effect = PermissionError("revoked")
        revoked = await client.post(path, json=reference, headers=headers)
        assert revoked.status_code == 409


@pytest.mark.asyncio
async def test_private_workflow_binding_check_reads_current_database_state(
    db_session,
    test_project,
    monkeypatch,
):
    monkeypatch.setattr(settings, "secret_key", "synthetic-private-workflow-key")
    workflow_id, release_id = str(uuid4()), str(uuid4())
    agent = Agent(
        project_id=test_project.id,
        name="订单员工",
        model="fixture",
        is_active=True,
        is_default=False,
    )
    db_session.add(agent)
    await db_session.flush()
    binding = AgentWorkflow(
        agent_id=agent.id,
        workflow_id=workflow_id,
        enabled=True,
        release_version=2,
        release_id=release_id,
        release_digest="a" * 64,
    )
    db_session.add(binding)
    await db_session.commit()
    application = FastAPI()
    application.include_router(endpoint.router, prefix="/api/v1/agents")

    async def read_db():
        yield db_session

    application.dependency_overrides[get_db] = read_db
    reference = {
        "project_id": str(test_project.id),
        "agent_id": str(agent.id),
        "workflow_id": workflow_id,
        "release_version": 2,
        "release_id": release_id,
        "release_digest": "a" * 64,
    }
    headers = {
        "X-Internal-API-Key": "synthetic-private-workflow-key",
        "X-Project-Id": str(test_project.id),
    }
    path = "/api/v1/agents/workflow-binding-check"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        accepted = await client.post(path, json=reference, headers=headers)
        assert accepted.status_code == 200

        binding.enabled = False
        await db_session.commit()
        revoked = await client.post(path, json=reference, headers=headers)
        assert revoked.status_code == 409
