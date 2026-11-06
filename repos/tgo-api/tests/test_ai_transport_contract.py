"""Wire, permit, multipart and stream contracts for the existing AI SDK."""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel, SecretStr

from app.core.config import settings
from app.services.ai_client import AIServiceClient
from app.services.ai_usage_runtime import ReplyPermit, bind_permit

MARKER = "synthetic-private-ai-transport"


class SyntheticModel(BaseModel):
    value: str
    optional: str | None = None


class BrokenModel:
    def model_dump(self, **options):
        raise ValueError("synthetic dump failure")

    def __str__(self):
        return "custom-model-string"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (False, False),
        (0, 0),
        (0.0, 0.0),
        ("", ""),
        (date(2026, 11, 6), "2026-11-06"),
        (
            datetime(2026, 11, 6, tzinfo=timezone.utc),
            "2026-11-06T00:00:00+00:00",
        ),
        (("one", False), ["one", False]),
        ({7}, [7]),
        ({1: [0, False, None]}, {"1": [0, False, None]}),
        (SyntheticModel(value="kept"), {"value": "kept"}),
        (BrokenModel(), "custom-model-string"),
    ],
)
def test_serialization_keeps_existing_primitives_and_fallback(value, expected):
    assert AIServiceClient()._to_jsonable(value) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["json", "content", "multipart"])
@pytest.mark.parametrize("with_permit", [False, True])
async def test_transport_keeps_payloads_and_durable_billing_scope(
    monkeypatch,
    mode,
    with_permit,
):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_INTERNAL_TOKEN", SecretStr(MARKER))
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
    service = AIServiceClient()
    service.base_url = "http://ai.internal"
    service.timeout = 12
    project, reservation, lease = uuid4(), uuid4(), uuid4()
    permit = (
        ReplyPermit(project, reservation, lease, False)
        if with_permit
        else None
    )
    options = {
        "json": {"json_data": {"id": project, "zero": 0, "false": False}},
        "content": {"content": "# 合成技能\n保留原始文本\n"},
        "multipart": {
            "form_data": {"metadata": '{"media_id":"synthetic"}'},
            "files": {
                "file": ("sample.png", b"synthetic-media-bytes", "image/png")
            },
        },
    }[mode]
    with bind_permit(permit):
        result = await service._make_request(
            "POST",
            "/synthetic",
            **options,
            timeout=15,
            extra_headers={"X-Project-Id": str(project)},
            params={"tags": ["one", "two"], "limit": 0},
        )
    assert result.json() == {"ok": True}
    assert options_seen == [{"timeout": 15, "trust_env": False}]
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["X-SaaS-Service-Token"] == MARKER
    assert request.headers["X-Project-Id"] == str(project)
    assert request.url.params.multi_items() == [
        ("tags", "one"),
        ("tags", "two"),
        ("limit", "0"),
    ]
    if with_permit:
        assert request.headers["X-AI-Reservation"] == str(reservation)
        assert request.headers["X-AI-Lease"] == str(lease)
    else:
        assert "X-AI-Reservation" not in request.headers
        assert "X-AI-Lease" not in request.headers
    if mode == "json":
        assert json.loads(request.content) == {
            "id": str(project),
            "zero": 0,
            "false": False,
        }
    elif mode == "content":
        assert request.content.decode() == options["content"]
    else:
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + request.headers["Content-Type"].encode()
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + request.content,
        )
        parts = {
            part.get_param("name", header="content-disposition"): part
            for part in message.iter_parts()
        }
        assert (
            parts["metadata"].get_payload(decode=True)
            == b'{"media_id":"synthetic"}'
        )
        assert (
            parts["file"].get_payload(decode=True) == b"synthetic-media-bytes"
        )
        assert parts["file"].get_filename() == "sample.png"
        assert parts["file"].get_content_type() == "image/png"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_content", [False, True])
async def test_multipart_conflict_is_rejected_without_sending(
    monkeypatch, with_content
):
    original = httpx.AsyncClient
    sent = []

    def respond(request):
        sent.append(request)
        raise AssertionError("Conflicting body must not be sent")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(respond),
        ),
    )
    with pytest.raises(ValueError, match="Multipart and JSON"):
        await AIServiceClient()._make_request(
            "POST",
            "/synthetic",
            files={"file": ("f", b"x", "text/plain")},
            **({"content": "text"} if with_content else {"json_data": {}}),
        )
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "error_type,status", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)]
)
async def test_network_errors_keep_status_and_no_private_logs(
    monkeypatch,
    caplog,
    stream,
    error_type,
    status,
):
    original = httpx.AsyncClient
    attempts = []

    def fail(request):
        attempts.append(request)
        raise error_type(MARKER, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(fail),
        ),
    )
    service = AIServiceClient()
    service.base_url = "http://" + MARKER
    with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as error:
        if stream:
            await anext(
                service.run_supervisor_agent_stream("synthetic", str(uuid4()))
            )
        else:
            await service._make_request("POST", "/synthetic")
    assert error.value.status_code == status
    assert MARKER not in str(error.value.detail)
    assert len(attempts) == 1
    records = [r for r in caplog.records if r.name == "ai_client"]
    assert records
    assert all(MARKER not in str(record.__dict__) for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,expected",
    [
        (
            ': heartbeat\nevent: event\ndata: {"zero":0,\n'
            'data: "false":false}\n\n',
            [("event", {"zero": 0, "false": False})],
        ),
        (
            "data: plain text\n\nevent: final\ndata: null",
            [("message", "plain text"), ("final", None)],
        ),
        (
            "event: ignored\n\ndata: 0\n\ndata: false\n\n",
            [("message", 0), ("message", False)],
        ),
    ],
)
async def test_stream_frames_preserve_multiline_and_final_event(
    monkeypatch, text, expected
):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=text),
            ),
        ),
    )
    result = [
        event
        async for event in AIServiceClient().run_supervisor_agent_stream(
            "synthetic",
            str(uuid4()),
        )
    ]
    assert result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_tool_catalog_updates_owned_identity_or_creates_with_project(
    monkeypatch,
    existing,
):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "list_tools",
        AsyncMock(
            return_value=(
                [{"id": "owned-tool", "name": "lookup"}] if existing else []
            )
        ),
    )
    update = AsyncMock(return_value={"id": "owned-tool"})
    create = AsyncMock(return_value={"id": "new-tool"})
    monkeypatch.setattr(service, "update_tool", update)
    monkeypatch.setattr(service, "create_tool", create)
    payload = {"name": "lookup", "config": {"enabled": False}}
    result = await service.create_or_update_tool("owned-project", payload)
    if existing:
        assert result == {"id": "owned-tool"}
        update.assert_awaited_once_with("owned-project", "owned-tool", payload)
        create.assert_not_awaited()
    else:
        assert result == {"id": "new-tool"}
        create.assert_awaited_once_with(
            {**payload, "project_id": "owned-project"}
        )
        update.assert_not_awaited()
    assert payload == {"name": "lookup", "config": {"enabled": False}}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_id", [None, 7, []])
async def test_tool_catalog_refuses_invalid_identity_before_update(
    monkeypatch, tool_id
):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "list_tools",
        AsyncMock(
            return_value=[
                {"id": tool_id, "name": "lookup"},
            ]
        ),
    )
    update = AsyncMock()
    monkeypatch.setattr(service, "update_tool", update)
    with pytest.raises(HTTPException) as error:
        await service.create_or_update_tool(
            "owned-project", {"name": "lookup"}
        )
    assert error.value.status_code == 502
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefix_removal_only_passes_matching_owned_ids(monkeypatch):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "list_tools",
        AsyncMock(
            return_value=[
                {"id": "matching", "name": "prefix_lookup"},
                {"id": "other", "name": "unrelated"},
            ]
        ),
    )
    remove = AsyncMock()
    monkeypatch.setattr(service, "delete_tool", remove)
    await service.delete_tools_by_prefix("owned-project", "prefix_")
    remove.assert_awaited_once_with("owned-project", "matching")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        {"id": None, "name": "prefix_lookup"},
        {"id": "owned", "name": []},
    ],
)
async def test_prefix_removal_refuses_invalid_identity(monkeypatch, row):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    monkeypatch.setattr(service, "list_tools", AsyncMock(return_value=[row]))
    remove = AsyncMock()
    monkeypatch.setattr(service, "delete_tool", remove)
    with pytest.raises(HTTPException) as error:
        await service.delete_tools_by_prefix("owned-project", "prefix_")
    assert error.value.status_code == 502
    remove.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "delete_agent",
        "clear_session_memory",
        "set_agent_tool_enabled",
        "set_agent_collection_enabled",
        "set_agent_workflow_enabled",
    ],
)
async def test_empty_mutation_responses_and_errors_keep_contract(
    monkeypatch, operation
):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    send = AsyncMock(return_value=httpx.Response(204))
    monkeypatch.setattr(service, "_make_request", send)
    args = ["owned-project", "resource"]
    if operation.startswith("set_"):
        args.extend(["binding", False])
    assert await getattr(service, operation)(*args) is None
    assert send.await_args.kwargs["params"]["project_id"] == "owned-project"
    detail = {"detail": "controlled conflict"}
    send.return_value = httpx.Response(409, json=detail)
    with pytest.raises(HTTPException) as error:
        await getattr(service, operation)(*args)
    assert error.value.status_code == 409 and error.value.detail == detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,expected",
    [
        ({"exists": True}, True),
        ({"exists": False}, False),
        ({}, False),
        ([], False),
        (None, False),
    ],
)
async def test_agent_existence_keeps_existing_normal_defaults(
    monkeypatch,
    value,
    expected,
):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    monkeypatch.setattr(
        service,
        "_make_request",
        AsyncMock(
            return_value=httpx.Response(200, json=value),
        ),
    )
    assert await service.check_agents_exist("owned-project") is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception", [HTTPException(503, MARKER), RuntimeError(MARKER)]
)
async def test_agent_lookup_errors_keep_fallback_and_logs_safe(
    monkeypatch,
    caplog,
    exception,
):
    from unittest.mock import AsyncMock

    service = AIServiceClient()
    monkeypatch.setattr(
        service, "_make_request", AsyncMock(side_effect=exception)
    )
    with caplog.at_level(logging.INFO):
        assert await service.check_agents_exist(MARKER) is True
    records = [r for r in caplog.records if r.name == "ai_client"]
    assert records and all(MARKER not in str(r.__dict__) for r in records)
