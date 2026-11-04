"""Recovery validates the original receipt without interpreting live activation as an outcome."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)
from app.services import agent_activation_request_client as client


def fixture():
    project, actor, agent, key = [uuid4() for _ in range(4)]
    command = AgentActivationRequest.model_validate(
        {
            "request_key": key,
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
        project_id=project,
        requested_by=actor,
        agent_id=agent,
        request_key=key,
        status="completed",
        command=command,
        finished_at=datetime.now(UTC),
    )
    transport = AsyncMock()
    transport._trial_headers = lambda p, a: {
        "X-Project-Id": str(p),
        "X-Staff-Id": str(a),
    }
    transport._make_request.return_value = httpx.Response(
        200, json=receipt.model_dump(mode="json")
    )
    return transport, project, actor, agent, key, command, receipt


@pytest.mark.asyncio
async def test_original_receipt_transport_uses_login_scope_and_unchanged_intent():
    transport, project, actor, agent, key, command, receipt = fixture()
    assert await client.submit(transport, project, actor, agent, command) == receipt
    call = transport._make_request.call_args
    assert call.args == ("POST", f"/api/v1/agents/{agent}/activation-requests")
    assert call.kwargs["json_data"] == command.model_dump(mode="json")
    assert call.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)
    assert await client.lookup(transport, project, actor, agent, key) == receipt
    assert transport._make_request.call_args.args == (
        "GET",
        f"/api/v1/agents/{agent}/activation-requests/{key}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "project_id",
        "requested_by",
        "agent_id",
        "request_key",
        "scope",
        "command",
        "finished_at",
        "can_enable",
    ],
)
async def test_wrong_or_incomplete_receipt_cannot_clear_an_unknown_request(field):
    transport, project, actor, agent, key, command, receipt = fixture()
    body = receipt.model_dump(mode="json")
    if field in {"project_id", "requested_by", "agent_id", "request_key"}:
        body[field] = str(uuid4())
    elif field == "scope":
        body[field] = "employee_activation"
    elif field == "command":
        body[field]["expected"]["configuration_fingerprint"] = "d" * 64
    else:
        body.pop(field)
    transport._make_request.return_value = httpx.Response(200, json=body)
    with pytest.raises(HTTPException) as error:
        await client.submit(transport, project, actor, agent, command)
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_missing_result_is_a_scoped_receipt_and_never_causes_submission():
    transport, project, actor, agent, key, _, _ = fixture()
    receipt = AgentActivationReceipt(
        project_id=project,
        requested_by=actor,
        agent_id=agent,
        request_key=key,
        status="not_found",
    )
    transport._make_request.return_value = httpx.Response(
        200, json=receipt.model_dump(mode="json")
    )
    assert await client.lookup(transport, project, actor, agent, key) == receipt
    assert transport._make_request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 500])
async def test_transport_errors_do_not_disclose_or_fabricate_an_outcome(status):
    transport, project, actor, agent, key, _, _ = fixture()
    transport._make_request.return_value = httpx.Response(
        status, json={"credential": "must-not-echo"}
    )
    with pytest.raises(HTTPException) as error:
        await client.lookup(transport, project, actor, agent, key)
    assert error.value.status_code == status and "must-not-echo" not in str(error.value)


@pytest.mark.asyncio
async def test_channel_order_does_not_turn_an_identical_original_retry_into_an_unknown_result():
    transport, project, actor, agent, _, command, receipt = fixture()
    first = command.expected.channels[0]
    command.expected.channels = [first, first.model_copy(update={"channel": "internal"})]
    receipt.command = command.model_copy(deep=True)
    retry = command.model_copy(deep=True)
    retry.expected.channels.reverse()
    transport._make_request.return_value = httpx.Response(200, json=receipt.model_dump(mode="json"))
    assert await client.submit(transport, project, actor, agent, retry) == receipt
