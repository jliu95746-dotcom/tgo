"""Reject malformed Workflow responses and keep private logs safe."""

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.services.workflow_client import WorkflowServiceClient

MARKER = "synthetic-private-workflow-boundary"
OPERATIONS = [
    "list_workflows",
    "create_workflow",
    "get_workflow",
    "update_workflow",
    "duplicate_workflow",
    "validate_workflow_generic",
    "validate_workflow",
    "get_workflow_variables",
    "get_execution",
    "cancel_execution",
    "route_customer_service",
    "execute_workflow",
]


async def invoke(service, operation):
    if operation == "list_workflows":
        return await service.list_workflows("project")
    if operation == "create_workflow":
        return await service.create_workflow("project", {"name": "synthetic"})
    if operation == "update_workflow":
        return await service.update_workflow("workflow", "project", {})
    if operation == "validate_workflow_generic":
        return await service.validate_workflow_generic("project", {})
    if operation == "route_customer_service":
        return await service.route_customer_service({"project_id": "project"})
    if operation == "execute_workflow":
        return await service.execute_workflow("workflow", "project", {})
    return await getattr(service, operation)("workflow", "project")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("payload", [None, [], MARKER, 17])
async def test_object_operations_refuse_wrong_success_shape(
    monkeypatch, operation, payload
):
    service = WorkflowServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(return_value=httpx.Response(200, json=payload)),
    )
    with pytest.raises(HTTPException) as error:
        await invoke(service, operation)
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_object_operations_keep_normal_json(monkeypatch, operation):
    data = {"value": [0, False, "", None], "extra": {"unknown": "kept"}}
    service = WorkflowServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(return_value=httpx.Response(200, json=data)),
    )
    assert await invoke(service, operation) == data


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_non_json_success_is_controlled(monkeypatch, operation):
    service = WorkflowServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(return_value=httpx.Response(200, text=MARKER)),
    )
    with pytest.raises(HTTPException) as error:
        await invoke(service, operation)
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"items": []}, [None], [{}, MARKER]])
async def test_execution_list_requires_object_items(monkeypatch, payload):
    service = WorkflowServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(return_value=httpx.Response(200, json=payload)),
    )
    with pytest.raises(HTTPException) as error:
        await service.list_workflow_executions("workflow", "project")
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
async def test_empty_list_no_content_and_error_contracts_stay(monkeypatch):
    service = WorkflowServiceClient()
    send = AsyncMock(return_value=httpx.Response(200, json=[]))
    monkeypatch.setattr(service, "_make_request", send)
    assert await service.list_workflow_executions("workflow", "project") == []
    send.return_value = httpx.Response(204)
    assert await service.delete_workflow("workflow", "project") is None
    detail = {"detail": {"message": "controlled conflict", "errors": []}}
    send.return_value = httpx.Response(409, json=detail)
    with pytest.raises(HTTPException) as error:
        await service.get_workflow("workflow", "project")
    assert error.value.status_code == 409 and error.value.detail == detail


@pytest.mark.asyncio
async def test_workflow_request_logs_keep_private_values_out(
    monkeypatch, caplog
):
    original = httpx.AsyncClient

    def reply(request):
        response = httpx.Response(200, json={"ok": True})
        response.elapsed = timedelta(milliseconds=1)
        return response

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options, transport=httpx.MockTransport(reply)
        ),
    )
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    with caplog.at_level(logging.INFO):
        await service._make_request(
            "POST",
            "/" + MARKER,
            json_data={"private": MARKER},
            params={"digest": MARKER},
            extra_headers={"X-Private": MARKER},
        )
    records = [
        record for record in caplog.records if "workflow_client" in record.name
    ]
    assert records
    assert all(MARKER not in record.getMessage() for record in records)
    assert all(MARKER not in str(record.__dict__) for record in records)
