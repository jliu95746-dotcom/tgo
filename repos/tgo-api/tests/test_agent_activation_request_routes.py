"""The public proxy derives receipt ownership from login and rejects extra authority."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.agent_configuration_access import require_agent_configuration_actor
from app.api.v1.endpoints.agent_activation_requests import router
from app.core.exceptions import validation_exception_handler
from app.models.staff import Staff
from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)
from app.services.ai_client import ai_client


@pytest.fixture
def application(monkeypatch):
    actor = Staff(id=uuid4(), project_id=uuid4(), role="admin", account_enabled=True)
    agent = uuid4()
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
    receipt = AgentActivationReceipt(
        project_id=actor.project_id,
        requested_by=actor.id,
        agent_id=agent,
        request_key=command.request_key,
        status="completed",
        command=command,
        finished_at=datetime.now(UTC),
    )
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/agents")

    def current():
        if actor.role != "admin":
            raise HTTPException(403, "管理员权限已撤销")
        return actor

    app.dependency_overrides[require_agent_configuration_actor] = current
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    submit, lookup = AsyncMock(return_value=receipt), AsyncMock(return_value=receipt)
    monkeypatch.setattr(ai_client, "submit_agent_activation_request", submit)
    monkeypatch.setattr(ai_client, "lookup_agent_activation_request", lookup)
    return app, actor, agent, command, submit, lookup


@pytest.mark.asyncio
async def test_public_recovery_keeps_original_key_and_login_identity(application):
    app, actor, agent, command, submit, lookup = application
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/v1/ai/agents/{agent}/activation-requests"
        assert (
            await client.post(path, json=command.model_dump(mode="json"))
        ).status_code == 200
        assert (
            await client.get(path + "/" + str(command.request_key))
        ).status_code == 200
    assert submit.call_args.args == (actor.project_id, actor.id, agent, command)
    assert lookup.call_args.args == (
        actor.project_id,
        actor.id,
        agent,
        command.request_key,
    )


@pytest.mark.asyncio
async def test_public_recovery_rechecks_role_before_both_operations(application):
    app, actor, agent, command, submit, lookup = application
    actor.role = "user"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/v1/ai/agents/{agent}/activation-requests"
        assert (
            await client.post(path, json=command.model_dump(mode="json"))
        ).status_code == 403
        assert (
            await client.get(path + "/" + str(command.request_key))
        ).status_code == 403
    assert not submit.called and not lookup.called


@pytest.mark.asyncio
async def test_public_intent_cannot_smuggle_credentials_actor_or_enable_authority(
    application,
):
    app, _, agent, command, submit, _ = application
    body = {
        **command.model_dump(mode="json"),
        "requested_by": str(uuid4()),
        "credential": "must-not-echo",
        "can_enable": True,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            f"/v1/ai/agents/{agent}/activation-requests", json=body
        )
    assert (
        result.status_code == 422
        and "must-not-echo" not in result.text
        and not submit.called
    )
