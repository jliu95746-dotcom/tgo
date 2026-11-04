"""Workflow queries reuse formal customer and employee authorization."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from pydantic import SecretStr, ValidationError

from app.exceptions import AuthorizationError, ExternalServiceError
from app.schemas.agent_business_execution import (
    BusinessExecutionAuthority, BusinessExecutionReceipt,
)
from app.schemas.workflow_business_query import WorkflowBusinessQueryRequest
from app.services import workflow_business_query as module
from tests.test_agent_business_execution_protocol import command
from tests.test_business_execution_context import fixture


def setup_query():
    check, principal = fixture()
    reference = command().reference.model_copy(update={
        "project_id": check.project_id,
        "channel": check.channel,
    })
    binding = SimpleNamespace(
        id=uuid4(), project_id=check.project_id,
        agent_id=reference.agent_id,
        connection_id=principal.bindings[0].connection_id,
        operations=["order_query"], enabled=True, revision=2,
    )
    request = WorkflowBusinessQueryRequest(
        project_id=check.project_id, agent_id=reference.agent_id,
        connection_id=binding.connection_id, operation="order_query",
        workflow_execution_id=uuid4(),
        visitor_session_id=principal.visitor_session_id,
        order_no=SecretStr("ABCD-1234"), context=check,
    )
    return request, principal, reference, binding


def receipt_for(value):
    now = datetime.now(UTC)
    identity = value.identity()
    authority = BusinessExecutionAuthority(
        **identity, actor_id=uuid4(), phase="after_query",
        connection_revision=1, configuration_revision=1,
        authorization_revision=1, resource_revision="a" * 64,
        report_id=uuid4(), review_id=uuid4(), configuration_test_id=uuid4(),
        checked_at=now, expires_at=now + timedelta(minutes=1),
    )
    return BusinessExecutionReceipt(
        **identity, authority=authority, outcome="passed",
        summary={"status": "已发货", "carrier": "顺丰"},
        error_code=None, checked_at=now,
    )


def test_private_query_schema_never_serializes_customer_secrets():
    request, _, _, _ = setup_query()
    assert request.private_payload()["order_no"] == "ABCD-1234"
    assert request.private_payload()["context"]["context_token"] == "private-context-fixture"
    assert "ABCD-1234" not in request.model_dump_json()
    assert "private-context-fixture" not in request.model_dump_json()
    assert "ABCD-1234" not in repr(request)


def test_workflow_query_rejects_invalid_order_number():
    request, _, _, _ = setup_query()
    payload = request.private_payload()
    payload["order_no"] = "ABC 123"
    with pytest.raises(ValidationError):
        WorkflowBusinessQueryRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_workflow_query_returns_only_bounded_receipt(monkeypatch):
    request, principal, reference, binding = setup_query()
    db = Mock(scalar=AsyncMock(return_value=binding))
    monkeypatch.setattr(module.api_service_client, "get_business_request_context", AsyncMock(return_value=principal))
    monkeypatch.setattr(module, "read_reference", AsyncMock(return_value=reference))
    query = AsyncMock(side_effect=receipt_for)
    monkeypatch.setattr(module.api_service_client, "query_business_execution", query)

    result = await module.WorkflowBusinessQueryService(db).execute(request)

    sent = query.await_args.args[0]
    assert sent.reference == reference
    assert sent.connection_id == binding.connection_id
    assert sent.binding_id == binding.id and sent.binding_revision == binding.revision
    assert sent.context == request.context
    assert result.summary.status == "已发货"
    assert result.workflow_execution_id == request.workflow_execution_id
    assert "ABCD-1234" not in result.model_dump_json()
    assert "private-context-fixture" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["session", "binding", "operation", "digest"])
async def test_foreign_or_stale_scope_stops_before_provider(monkeypatch, fault):
    request, principal, reference, binding = setup_query()
    if fault == "session":
        principal = principal.model_copy(update={"visitor_session_id": uuid4()})
    elif fault == "binding":
        binding.enabled = False
    elif fault == "operation":
        binding.operations = ["logistics_query"]
    elif fault == "digest":
        principal = principal.model_copy(update={"context_digest": "f" * 64})
    db = Mock(scalar=AsyncMock(return_value=binding))
    monkeypatch.setattr(module.api_service_client, "get_business_request_context", AsyncMock(return_value=principal))
    monkeypatch.setattr(module, "read_reference", AsyncMock(return_value=reference))
    query = AsyncMock()
    monkeypatch.setattr(module.api_service_client, "query_business_execution", query)

    with pytest.raises(AuthorizationError):
        await module.WorkflowBusinessQueryService(db).execute(request)
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_mismatched_query_receipt_is_never_reported_as_success(monkeypatch):
    request, principal, reference, binding = setup_query()
    db = Mock(scalar=AsyncMock(return_value=binding))
    monkeypatch.setattr(module.api_service_client, "get_business_request_context", AsyncMock(return_value=principal))
    monkeypatch.setattr(module, "read_reference", AsyncMock(return_value=reference))
    monkeypatch.setattr(module.api_service_client, "query_business_execution", AsyncMock(
        side_effect=lambda value: receipt_for(value).model_copy(update={"query_id": uuid4()}),
    ))

    with pytest.raises(ExternalServiceError):
        await module.WorkflowBusinessQueryService(db).execute(request)


@pytest.mark.asyncio
async def test_not_found_stays_a_typed_failure(monkeypatch):
    request, principal, reference, binding = setup_query()
    db = Mock(scalar=AsyncMock(return_value=binding))
    monkeypatch.setattr(module.api_service_client, "get_business_request_context", AsyncMock(return_value=principal))
    monkeypatch.setattr(module, "read_reference", AsyncMock(return_value=reference))
    monkeypatch.setattr(module.api_service_client, "query_business_execution", AsyncMock(
        side_effect=lambda value: receipt_for(value).model_copy(update={
            "outcome": "failed", "summary": None, "error_code": "not_found",
        }),
    ))

    result = await module.WorkflowBusinessQueryService(db).execute(request)
    assert result.outcome == "failed"
    assert result.summary is None and result.error_code == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_key", "wrong_project", "wrong_channel"])
async def test_private_route_rejects_untrusted_scope(monkeypatch, fault):
    from app.api.v1 import agent_business_execution as route

    request, _, _, _ = setup_query()
    monkeypatch.setattr(route.settings, "secret_key", "local-test-key")
    execute = AsyncMock()
    monkeypatch.setattr(route, "WorkflowBusinessQueryService", lambda db: SimpleNamespace(execute=execute))
    headers = {
        "x-internal-api-key": "" if fault == "missing_key" else "local-test-key",
        "x-project-id": str(uuid4()) if fault == "wrong_project" else str(request.project_id),
        "x-execution-channel": "wecom_kf" if fault == "wrong_channel" else "web",
    }
    http_request = Request({
        "type": "http",
        "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
    })
    with pytest.raises(HTTPException) as error:
        await route.workflow_query(request, http_request, Mock())
    assert error.value.status_code == 403
    execute.assert_not_awaited()
