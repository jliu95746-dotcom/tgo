"""The quota bridge is internal-only and rejects caller-supplied ownership."""
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.internal import internal_app
from app.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["run", "recover"])
async def test_workflow_bridge_requires_internal_identity(
    monkeypatch, operation
):
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-workflow-bridge")
    command = {
        "project_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "node_id": "agent",
        "payload": {"agent_id": str(uuid4()), "message": "synthetic"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=internal_app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                f"/internal/workflow-ai/{operation}", json=command
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/internal/workflow-ai/{operation}",
                json=command,
                headers={"X-Internal-API-Key": "wrong"},
            )
        ).status_code == 403
        altered = {**command, "owner": {"unmetered": True}}
        response = await client.post(
            f"/internal/workflow-ai/{operation}",
            json=altered,
            headers={"X-Internal-API-Key": "synthetic-workflow-bridge"},
        )
        assert response.status_code == 422
    assert not any(
        getattr(route, "path", "").startswith("/internal/workflow-ai")
        for route in app.routes
    )
