"""Private request identity reaches the real supervisor preparation, never model data."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.exceptions import AuthorizationError, ExternalServiceError
from app.schemas.business_execution_context import (
    BusinessContextCheck,
    BusinessRequestPrincipal,
    business_request_fingerprint,
)
from app.services.business_context_client import verify_context


def fixture():
    project, visitor, request = uuid4(), uuid4(), uuid4()
    token = SecretStr("private-context-fixture")
    check = BusinessContextCheck(
        project_id=project,
        visitor_id=visitor,
        channel="web",
        request_id=request,
        request_fingerprint=business_request_fingerprint(visitor, "客户咨询"),
        context_token=token,
    )
    result = BusinessRequestPrincipal(
        **check.model_dump(),
        platform_id=uuid4(),
        visitor_session_id=uuid4(),
        source_message_id="actual-message",
        response_client_msg_no="actual-attempt",
        bindings=[
            {
                "connection_id": uuid4(),
                "binding_id": uuid4(),
                "binding_revision": 1,
                "policy_revision": 1,
                "connection_configuration_revision": 1,
                "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            }
        ],
        context_digest=hashlib.sha256(token.get_secret_value().encode()).hexdigest(),
        checked_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=4),
    )
    return check, result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "scope",
        "project",
        "digest",
        "stale",
        "extra",
        "grant",
        "missing",
        "denied",
    ],
)
async def test_private_owner_receipt_is_current_complete_and_exact(fault):
    check, result = fixture()
    body = result.model_dump(mode="json")
    if fault == "scope":
        body["scope"] = "configuration_test"
    elif fault == "project":
        body["project_id"] = str(uuid4())
    elif fault == "digest":
        body["context_digest"] = "b" * 64
    elif fault == "stale":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    elif fault == "extra":
        body["customer_id"] = "private-upstream-customer"
    elif fault == "grant":
        body["can_enable"] = True
    elif fault == "missing":
        body.pop("scope")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403 if fault == "denied" else 200, json=body)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        if fault is None:
            assert (
                await verify_context(client, "http://fixture.invalid/internal", check)
                == result
            )
        else:
            with pytest.raises((AuthorizationError, ExternalServiceError)) as error:
                await verify_context(client, "http://fixture.invalid/internal", check)
            assert "private-upstream-customer" not in str(error.value)
            assert check.context_token.get_secret_value() not in str(error.value)


@pytest.mark.asyncio
async def test_supervisor_preparation_consumes_verified_context_without_serializing_it(
    monkeypatch,
):
    from app.runtime.supervisor.application.service import SupervisorRuntimeService
    from app.runtime.supervisor.infrastructure.services import AIServiceClient
    from app.services.api_service import api_service_client
    from app.schemas.agent_run import SupervisorRunRequest
    from tests.test_agent_knowledge_trial import employee

    check, result = fixture()
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    internal = employee().model_copy(update={"project_id": str(check.project_id)})

    @asynccontextmanager
    async def service():
        yield SimpleNamespace(db=Mock())

    monkeypatch.setattr(runtime, "_agent_service_context", service)
    monkeypatch.setattr(
        runtime, "_authorize_execution", AsyncMock(return_value=internal)
    )
    monkeypatch.setattr(
        runtime, "_reference_for_execution", AsyncMock(return_value=None)
    )
    loader = AsyncMock(return_value=internal)
    monkeypatch.setattr(AIServiceClient, "get_agent", loader)
    owner = AsyncMock(return_value=result)
    monkeypatch.setattr(api_service_client, "get_business_request_context", owner)
    payload = SupervisorRunRequest(
        agent_id=str(internal.id),
        message="客户咨询",
        user_id=str(check.visitor_id),
        disable_tools=True,
        knowledge_channel="web",
    )
    headers = {
        "X-Request-ID": str(uuid4()),
        "X-Execution-Channel": "web",
        "X-Business-Request-ID": str(check.request_id),
        "X-Business-Request-Context": check.context_token.get_secret_value(),
    }
    context, _ = await runtime._prepare_context(payload, check.project_id, headers)
    assert (
        context.business_principal == result
        and context.business_context_token == check.context_token
    )
    assert owner.await_args.args[0] == check
    assert (
        "business_principal" not in context.model_dump()
        and "business_context_token" not in context.model_dump()
    )
    assert check.context_token.get_secret_value() not in repr(context)
    assert "actual-message" not in context.model_dump_json()
    owner.side_effect = AuthorizationError("客户身份已撤销")
    loader.reset_mock()
    with pytest.raises(AuthorizationError):
        await runtime._prepare_context(payload, check.project_id, headers)
    loader.assert_not_awaited()
