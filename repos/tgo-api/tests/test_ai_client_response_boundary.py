"""AI API responses cannot silently change the client's public shapes."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.services.ai_client import AIServiceClient

MARKER = "synthetic-private-ai-response"
OBJECT_OPERATIONS = (
    "list_agents",
    "create_agent",
    "get_agent",
    "update_agent",
    "create_tool",
    "update_tool",
    "delete_tool",
    "chat_completions",
    "import_skill",
    "create_skill",
    "get_skill",
    "update_skill",
    "toggle_skill",
)


async def call(service, operation):
    project = str(uuid4())
    if operation in {"create_agent", "import_skill", "create_skill"}:
        return await getattr(service, operation)(project, {})
    if operation in {
        "update_agent",
        "update_tool",
        "update_skill",
        "toggle_skill",
    }:
        return await getattr(service, operation)(project, "resource", {})
    if operation == "create_tool":
        return await service.create_tool({"name": "synthetic"})
    if operation == "chat_completions":
        return await service.chat_completions(project, "provider", "model", [])
    if operation == "list_agents":
        return await service.list_agents(project)
    return await getattr(service, operation)(project, "resource")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OBJECT_OPERATIONS)
@pytest.mark.parametrize("value", [None, [], MARKER, 7])
async def test_object_methods_reject_wrong_success_shapes(
    monkeypatch,
    operation,
    value,
):
    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(200, json=value),
        ),
    )
    with pytest.raises(HTTPException) as error:
        await call(service, operation)
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OBJECT_OPERATIONS)
async def test_object_methods_preserve_zero_false_and_extra_fields(
    monkeypatch,
    operation,
):
    value = {"value": [0, False, "", None], "extra": {"unknown": "kept"}}
    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(200, json=value),
        ),
    )
    assert await call(service, operation) == value


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OBJECT_OPERATIONS)
async def test_object_methods_refuse_non_json_success(monkeypatch, operation):
    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(200, text=MARKER),
        ),
    )
    with pytest.raises(HTTPException) as error:
        await call(service, operation)
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list_tools", "list_skills"])
@pytest.mark.parametrize("value", [{"items": []}, [None], [{}, MARKER]])
async def test_collection_methods_reject_invalid_items(
    monkeypatch, operation, value
):
    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(200, json=value),
        ),
    )
    with pytest.raises(HTTPException) as error:
        await getattr(service, operation)(str(uuid4()))
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [0, 1, "false", [], {}])
async def test_agent_existence_always_returns_a_boolean(monkeypatch, exists):
    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(200, json={"exists": exists}),
        ),
    )
    assert await service.check_agents_exist(str(uuid4())) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("is_json", [False, True])
async def test_stream_errors_parse_json_and_keep_private_bodies_out_of_logs(
    monkeypatch,
    caplog,
    is_json,
):
    original = httpx.AsyncClient
    detail = {"detail": {"code": "controlled", "private": MARKER}}

    def respond(request):
        return httpx.Response(
            403, **({"json": detail} if is_json else {"text": MARKER})
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(respond),
        ),
    )
    service = AIServiceClient()
    service.base_url = "http://ai.internal"
    with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as error:
        await anext(service.run_supervisor_agent_stream("test", str(uuid4())))
    assert error.value.status_code == 403
    if is_json:
        assert error.value.detail == detail
    else:
        assert MARKER not in str(error.value.detail)
    records = [r for r in caplog.records if r.name == "ai_client"]
    assert records
    assert all(MARKER not in str(r.__dict__) for r in records)


@pytest.mark.asyncio
async def test_non_stream_errors_do_not_forward_plain_text(monkeypatch):
    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(503, text=MARKER),
        ),
    )
    with pytest.raises(HTTPException) as error:
        await service.get_agent(str(uuid4()), "resource")
    assert error.value.status_code == 503
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[None], [MARKER], [{"id": "one"}, 7]])
async def test_agent_list_rejects_invalid_rows_before_device_enrichment(
    monkeypatch,
    rows,
):
    from app.api.v1.endpoints import ai_agents as routes

    monkeypatch.setattr(
        routes.ai_client,
        "list_agents",
        AsyncMock(
            return_value={"data": rows},
        ),
    )
    enrich = AsyncMock()
    monkeypatch.setattr(routes, "_enrich_agents_with_devices", enrich)
    with pytest.raises(HTTPException) as error:
        await routes.list_agents(
            model=None,
            is_default=None,
            bound_device_id=None,
            limit=20,
            offset=0,
            project_and_api_key=(SimpleNamespace(id=uuid4()), "test"),
        )
    assert error.value.status_code == 502
    enrich.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, False, 0, MARKER, []])
async def test_reply_controller_refuses_non_object_events_and_closes_source(
    monkeypatch,
    value,
):
    from app.services import ai_reply_control as control

    monkeypatch.setattr(
        control, "begin_ai_phase", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(control, "check_current_chat_access", lambda: None)
    closed = []

    async def source(phase):
        try:
            yield "event", value
        finally:
            closed.append(True)

    with pytest.raises(HTTPException) as error:
        await anext(control.tracked_ai_stream(source))
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)
    assert closed == [True]
