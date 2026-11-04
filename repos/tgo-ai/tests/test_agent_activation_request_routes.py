"""Private receipt routes require the trusted principal even for historical reads."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.agent_activation_requests import get_service, router
from app.config import settings
from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)


@pytest.mark.asyncio
async def test_private_receipt_endpoints_are_never_anonymous(monkeypatch):
    project, actor, agent = [uuid4() for _ in range(3)]
    command = AgentActivationRequest.model_validate(
        {
            "request_key": uuid4(),
            "expected": {
                "checked_at": datetime.now(UTC),
                "configuration_fingerprint": "a" * 64,
                "model_owner_revision": "b" * 64,
                "channels": [
                    {
                        "template_id": "knowledge",
                        "template_version": 1,
                        "channel": "web",
                        "policy_revision": 1,
                        "requirements_revision": "c" * 64,
                    }
                ],
            },
        }
    )
    service = AsyncMock()
    service.submit.return_value = service.lookup.return_value = AgentActivationReceipt(
        project_id=project,
        requested_by=actor,
        agent_id=agent,
        request_key=command.request_key,
        status="completed",
        command=command,
        finished_at=datetime.now(UTC),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents")
    app.dependency_overrides[get_service] = lambda: service
    monkeypatch.setattr(settings, "secret_key", "synthetic-receipt-private-key")
    headers = {
        "X-Internal-API-Key": "synthetic-receipt-private-key",
        "X-Project-Id": str(project),
        "X-Staff-Id": str(actor),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/api/v1/agents/{agent}/activation-requests"
        assert (
            await client.post(path, json=command.model_dump(mode="json"))
        ).status_code == 403
        assert (
            await client.get(path + "/" + str(command.request_key))
        ).status_code == 403
        assert not service.submit.called and not service.lookup.called
        assert (
            await client.post(
                path, json=command.model_dump(mode="json"), headers=headers
            )
        ).status_code == 200
        assert (
            await client.get(path + "/" + str(command.request_key), headers=headers)
        ).status_code == 200
    assert service.submit.call_args.args == (project, actor, agent, command)
    assert service.lookup.call_args.args == (project, actor, agent, command.request_key)
