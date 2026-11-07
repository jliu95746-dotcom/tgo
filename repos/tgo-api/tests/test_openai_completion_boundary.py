"""Compatibility replies must contain the published answer and one outcome."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
import pytest

from app.api.v1.endpoints import chat
from app.schemas.chat import OpenAIChatCompletionRequest
from app.schemas.visitor_data_access import VisitorDataAccess
from app.utils.const import PUBLIC_AI_FAILURE_MESSAGE

PRIVATE = "synthetic-private-openai-provider"


def chunk(text, *, nested=True):
    payload = {"content_chunk": text}
    return {
        "event_type": "agent_content_chunk",
        "data": (
            {"event_type": "agent_content_chunk", "data": payload}
            if nested
            else payload
        ),
    }


def complete(kind="agent_response_complete", *, payload=None, nested=True):
    data = {} if payload is None else payload
    return {
        "event_type": kind,
        "data": {"event_type": kind, "data": data} if nested else data,
    }


@pytest.fixture
def runtime(monkeypatch):
    project = SimpleNamespace(id=uuid4())
    platform = SimpleNamespace(
        id=uuid4(), project_id=project.id, type="website"
    )
    visitor = SimpleNamespace(
        id=uuid4(), project_id=project.id, is_unassigned=False
    )
    access = VisitorDataAccess(
        visitor_id=visitor.id,
        project_id=project.id,
        platform_id=platform.id,
        platform_api_key=SecretStr("owned-platform"),
        im_token=SecretStr("owned-holder"),
    )
    state = SimpleNamespace(
        project=project,
        platform=platform,
        visitor=visitor,
        access=access,
        events=[
            chunk("已核验的商品资料回答"),
            complete(),
            complete("workflow_completed"),
        ],
        error=None,
        calls=[],
        closed=False,
        finalized=False,
    )
    state.db = MagicMock()
    state.db.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(staff_id=uuid4())
    )
    state.resolve = AsyncMock(return_value=(visitor, False, access))
    state.forward = AsyncMock(return_value="owned-source")
    state.nonstream = AsyncMock(
        return_value={"success": True, "content": "普通完整回答"}
    )

    async def guarded(factory, grant=None):
        assert grant is None or grant is access
        return await factory()

    async def source(**kwargs):
        state.calls.append(kwargs)
        try:
            for event in state.events:
                yield event
            if state.error is not None:
                raise state.error
            state.finalized = True
        finally:
            state.closed = True

    monkeypatch.setattr(
        chat.chat_service,
        "validate_platform_and_project",
        lambda *_: (platform, project),
    )
    monkeypatch.setattr(chat, "resolve_chat_visitor", state.resolve)
    monkeypatch.setattr(chat, "guarded_chat_call", guarded)
    monkeypatch.setattr(chat, "get_session_id", lambda *_: "owned-session")
    monkeypatch.setattr(
        chat.chat_service, "send_user_message_to_wukongim", state.forward
    )
    monkeypatch.setattr(chat.chat_service, "is_ai_disabled", lambda *_: False)
    monkeypatch.setattr(
        chat,
        "_resolve_platform_agent_kwargs",
        AsyncMock(
            return_value={
                "agent_id": "owned-agent",
                "knowledge_channel": "web",
            }
        ),
    )
    monkeypatch.setattr(
        chat.chat_service, "process_ai_stream_to_wukongim", source
    )
    monkeypatch.setattr(
        chat.chat_service, "handle_ai_response_non_stream", state.nonstream
    )
    return state


async def call(runtime, *, stream=True):
    request = OpenAIChatCompletionRequest(
        messages=[{"role": "user", "content": "商品是什么材质？"}],
        stream=stream,
        user=str(runtime.visitor.id),
    )
    return await chat.chat_completion_openai_compatible(
        request,
        Request(
            {
                "type": "http",
                "headers": [],
                "method": "POST",
                "path": "/chat/completions",
            }
        ),
        "owned-platform",
        runtime.db,
        HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="owned-holder"
        ),
        None,
    )


async def frames(runtime):
    response = await call(runtime)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    output = [
        part.decode() if isinstance(part, bytes) else part
        async for part in response.body_iterator
    ]
    parsed = []
    for part in output:
        assert part.startswith("data: ") and part.endswith("\n\n")
        value = part[6:-2]
        parsed.append(value if value == "[DONE]" else json.loads(value))
    return parsed


def answer(items):
    return "".join(
        item["choices"][0]["delta"]["content"] or ""
        for item in items
        if isinstance(item, dict) and "choices" in item
    )


def assert_success(items):
    assert items[-1] == "[DONE]" and items.count("[DONE]") == 1
    assert not any(
        isinstance(item, dict) and "error" in item for item in items
    )
    finishes = [
        item
        for item in items
        if isinstance(item, dict)
        and "choices" in item
        and item["choices"][0]["finish_reason"] == "stop"
    ]
    assert len(finishes) == 1
    assert (
        len(
            {
                item["id"]
                for item in items
                if isinstance(item, dict) and "choices" in item
            }
        )
        == 1
    )


def assert_failure(items):
    assert items[-1] == "[DONE]" and items.count("[DONE]") == 1
    errors = [
        item for item in items if isinstance(item, dict) and "error" in item
    ]
    assert len(errors) == 1
    assert errors[0]["error"] == {
        "message": PUBLIC_AI_FAILURE_MESSAGE,
        "type": "server_error",
    }
    assert not any(
        item["choices"][0]["finish_reason"] == "stop"
        for item in items
        if isinstance(item, dict) and "choices" in item
    )
    assert PRIVATE not in str(items)


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_published_reply_is_forwarded_once_and_upstream_finishes(
    runtime, nested
):
    runtime.events = [
        chunk("已核验回答", nested=nested),
        complete(nested=nested),
        complete("workflow_completed", nested=nested),
    ]
    items = await frames(runtime)
    assert answer(items) == "已核验回答"
    assert_success(items)
    assert runtime.closed and runtime.finalized
    call_kwargs = runtime.calls[0]
    assert call_kwargs["visitor_chat_access"] is runtime.access
    assert call_kwargs["project_id"] == str(runtime.project.id)
    assert call_kwargs["user_id"] == str(runtime.visitor.id)
    assert call_kwargs["agent_id"] == "owned-agent"
    assert call_kwargs["knowledge_channel"] == "web"
    assert call_kwargs["session_id"] == "owned-session"
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_deltas_keep_whitespace_and_unicode_exactly(runtime):
    runtime.events = [
        chunk("你好 ", nested=False),
        chunk("\n世界", nested=False),
        complete(nested=False),
    ]
    items = await frames(runtime)
    assert answer(items) == "你好 \n世界"
    assert_success(items)


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_nonstreaming_provider_final_answer_is_not_lost(runtime, nested):
    runtime.events = [
        complete(
            payload={"final_content": "一次性完整回答", "total_chunks": 0},
            nested=nested,
        )
    ]
    items = await frames(runtime)
    assert answer(items) == "一次性完整回答"
    assert_success(items)


@pytest.mark.asyncio
async def test_final_answer_does_not_duplicate_already_forwarded_text(runtime):
    runtime.events = [
        chunk("完整回答", nested=False),
        complete(payload={"final_content": "完整回答"}, nested=False),
    ]
    items = await frames(runtime)
    assert answer(items) == "完整回答"
    assert_success(items)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["workflow_failed", "agent_run_failed", "agent_response_error", "error"],
)
async def test_explicit_failures_are_visible_without_provider_details(
    runtime, kind, caplog
):
    runtime.events = [{"event_type": kind, "data": {"error_message": PRIVATE}}]
    assert_failure(await frames(runtime))
    assert PRIVATE not in caplog.text
    assert runtime.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [],
        [chunk("尚未完成", nested=False)],
        [complete(nested=False)],
        [{"event_type": "agent_execution_started", "data": {}}],
    ],
)
async def test_eof_without_complete_answer_does_not_report_success(
    runtime, events
):
    runtime.events = events
    assert_failure(await frames(runtime))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "raw",
        7,
        True,
        {"content_chunk": [PRIVATE]},
        {"content_chunk": {"private": PRIVATE}},
    ],
)
async def test_bad_chunk_shapes_are_rejected_without_echo(
    runtime, data, caplog
):
    runtime.events = [
        {"event_type": "agent_content_chunk", "data": data},
        complete(nested=False),
    ]
    assert_failure(await frames(runtime))
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_exceptions_and_late_failures_do_not_become_success(
    runtime, caplog
):
    runtime.events = [chunk("部分回答", nested=False)]
    runtime.error = RuntimeError(PRIVATE)
    assert_failure(await frames(runtime))
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_failure_after_completion_marker_is_still_not_success(runtime):
    runtime.events = [
        chunk("部分回答", nested=False),
        complete(nested=False),
        {"event_type": "workflow_failed", "data": {"error_message": PRIVATE}},
    ]
    assert_failure(await frames(runtime))


@pytest.mark.asyncio
async def test_plain_nonstreaming_completion_keeps_existing_response(runtime):
    result = await call(runtime, stream=False)
    assert result.object == "chat.completion"
    assert result.choices[0].message.content == "普通完整回答"
    assert result.choices[0].finish_reason == "stop"
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_visitor_rejection_precedes_messages_or_ai(runtime):
    runtime.resolve.side_effect = HTTPException(
        401, "Original visitor grant required"
    )
    with pytest.raises(HTTPException) as failure:
        await call(runtime)
    assert failure.value.status_code == 401
    runtime.forward.assert_not_awaited()
    assert runtime.calls == []
