"""Wire compatibility and failure handling for the Workflow transport."""

import json
import logging
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.workflow_client import WorkflowServiceClient

MARKER = "synthetic-private-workflow-transport"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("timeout", [None, 15.0])
async def test_transport_preserves_wire_settings(monkeypatch, raw, timeout):
    original = httpx.AsyncClient
    seen, options_seen = [], []

    def respond(request):
        seen.append(request)
        response = httpx.Response(200, json={"ok": True})
        response.elapsed = timedelta(milliseconds=1)
        return response

    def client(**options):
        options_seen.append(options)
        return original(**options, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    service.timeout = 9
    service.api_key = MARKER
    body = b'{ "signed": "unchanged" }\n'
    payload = {"inputs": {"value": [0, False, "", None]}}
    response = await service._make_request(
        "POST",
        "/probe",
        json_data=None if raw else payload,
        raw_body=body if raw else None,
        params={"tags": ["one", "two"], "project_id": "owned"},
        extra_headers={"X-Internal-API-Key": MARKER},
        timeout=timeout,
    )
    assert response.json() == {"ok": True}
    assert options_seen == [
        {
            "timeout": 9 if timeout is None else timeout,
            "trust_env": False,
        }
    ]
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/probe"
    assert request.url.params.multi_items() == [
        ("tags", "one"),
        ("tags", "two"),
        ("project_id", "owned"),
    ]
    assert request.headers["Authorization"] == "Bearer " + MARKER
    assert request.headers["X-Internal-API-Key"] == MARKER
    assert UUID(request.headers["X-Request-ID"]).version == 4
    assert request.headers["Content-Type"] == "application/json"
    if raw:
        assert request.content == body
    else:
        assert json.loads(request.content) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type,status",
    [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)],
)
async def test_transport_failure_is_controlled_without_private_logs(
    monkeypatch,
    caplog,
    error_type,
    status,
):
    original = httpx.AsyncClient
    calls = []

    def fail(request):
        calls.append(request)
        raise error_type(MARKER, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(fail),
        ),
    )
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as error:
        await service._make_request("POST", "/" + MARKER)
    assert error.value.status_code == status
    assert MARKER not in str(error.value.detail)
    assert len(calls) == 1  # A failure must not replay a business operation.
    records = [r for r in caplog.records if "workflow_client" in r.name]
    assert records
    assert all(MARKER not in str(record.__dict__) for record in records)


@pytest.mark.asyncio
async def test_conflicting_body_modes_are_rejected_before_sending(monkeypatch):
    def forbidden(**options):
        raise AssertionError("No request should be created")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    with pytest.raises(ValueError, match="both raw and JSON"):
        await WorkflowServiceClient()._make_request(
            "POST",
            "/probe",
            json_data={},
            raw_body=b"{}",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 404, 409, 422, 429, 500, 503])
@pytest.mark.parametrize("is_json", [False, True])
async def test_upstream_error_status_and_json_contracts(status, is_json):
    service = WorkflowServiceClient()
    detail = {"detail": {"message": "controlled", "errors": []}}
    response = httpx.Response(
        status,
        **({"json": detail} if is_json else {"text": MARKER}),
    )
    with pytest.raises(HTTPException) as error:
        await service._handle_response(response)
    assert error.value.status_code == status
    if is_json:
        assert error.value.detail == detail
    else:
        assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not-json", b'"\xff"', b"{broken"])
async def test_invalid_json_encoding_is_controlled(body):
    with pytest.raises(HTTPException) as error:
        await WorkflowServiceClient()._handle_response(
            httpx.Response(200, content=body),
        )
    assert error.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("is_json", [False, True])
async def test_stream_rejection_keeps_status_and_safe_detail(
    monkeypatch,
    is_json,
):
    original = httpx.AsyncClient

    def reject(request):
        return httpx.Response(
            503,
            **(
                {"json": {"detail": "controlled"}}
                if is_json
                else {"text": MARKER}
            ),
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(reject),
        ),
    )
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    stream = await service.execute_workflow("test", "owned", {"stream": True})
    with pytest.raises(HTTPException) as error:
        await anext(stream)
    assert error.value.status_code == 503
    assert MARKER not in str(error.value.detail)
    if is_json:
        assert error.value.detail == "controlled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["webhook", "schedule", "webhook_history", "schedule_history"],
)
async def test_management_reads_preserve_private_identity(
    monkeypatch, operation
):
    project = uuid4()
    fixtures = {
        "webhook": {
            "webhook": None,
            "binding_current": False,
            "registration_available": True,
        },
        "schedule": {
            "schedule": None,
            "binding_current": False,
            "execution_available": False,
        },
        "webhook_history": {
            "project_id": str(project),
            "workflow_id": "owned",
            "items": [],
            "total": 0,
            "limit": 7,
            "offset": 14,
            "has_more": False,
        },
        "schedule_history": {
            "items": [],
            "total": 0,
            "limit": 7,
            "offset": 14,
            "has_more": False,
        },
    }
    paths = {
        "webhook": "/v1/workflows/owned/webhook",
        "schedule": "/v1/workflows/owned/schedule",
        "webhook_history": "/v1/workflows/owned/webhook/deliveries",
        "schedule_history": "/v1/workflows/owned/schedule/occurrences",
    }
    monkeypatch.setattr(settings, "SECRET_KEY", MARKER)
    original = httpx.AsyncClient
    seen = []

    def reply(request):
        seen.append(request)
        response = httpx.Response(200, json=fixtures[operation])
        response.elapsed = timedelta(milliseconds=1)
        return response

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(reply),
        ),
    )
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    if operation == "webhook":
        result = await service.get_webhook("owned", project)
        assert result.webhook is None and result.registration_available
    elif operation == "schedule":
        result = await service.get_schedule("owned", project)
        assert result.schedule is None and not result.execution_available
    elif operation == "webhook_history":
        result = await service.get_webhook_deliveries("owned", project, 7, 14)
        assert result.items == [] and result.total == 0
    else:
        result = await service.list_schedule_occurrences(
            "owned", project, 7, 14
        )
        assert result.items == [] and result.total == 0
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET" and request.url.path == paths[operation]
    assert request.headers["X-Internal-API-Key"] == MARKER
    assert request.url.params["project_id"] == str(project)
    if operation.endswith("history"):
        assert request.url.params["limit"] == "7"
        assert request.url.params["offset"] == "14"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get_webhook", "get_schedule"])
async def test_management_reads_refuse_missing_private_identity(
    monkeypatch,
    operation,
):
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    with pytest.raises(HTTPException) as error:
        await getattr(WorkflowServiceClient(), operation)("owned", uuid4())
    assert error.value.status_code == 503
