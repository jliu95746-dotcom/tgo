"""Exercise the installed SDK parsers with synthetic HTTP transports."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.chat import (
    ChatCompletionRequest,
    ChatMessage,
    FunctionDefinition,
    ToolDefinition,
)
from app.services import chat_anthropic, chat_google
from app.services.chat_tool_calls import ToolCallStream
from app.services.chat_service import ChatCompletionError, ChatService
from app.services.quota_authorization import metered_execution


def request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        provider_id=uuid4(),
        model="fixture-model",
        auto_execute_tools=False,
        messages=[
            ChatMessage(role="system", content="企业规则"),
            ChatMessage(role="user", content="查询示例"),
        ],
        tools=[
            ToolDefinition(
                function=FunctionDefinition(
                    name="query",
                    parameters={"type": "object", "properties": {}},
                )
            )
        ],
        tool_choice="required",
    )


def anthropic_body(tool: bool = True) -> dict[str, object]:
    return {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "model": "fixture-model",
        "stop_sequence": None,
        "content": [
            {"type": "thinking", "thinking": "private", "signature": "sig"},
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "query",
                "input": {"number": 1},
            },
        ]
        if tool
        else [{"type": "text", "text": "已查询"}],
        "stop_reason": "tool_use" if tool else "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def google_body(tool: bool = True) -> dict[str, object]:
    return {
        "candidates": [
            {
                "index": 0,
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "query",
                                "args": {"number": 1},
                            },
                            "thoughtSignature": "c2ln",
                        },
                    ]
                    if tool
                    else [{"text": "已查询"}],
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 11,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 2,
            "totalTokenCount": 18,
        },
    }


def anthropic_client(handler: object) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key="fixture-key",
        base_url="https://vendor.example.test",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        ),
    )


def google_client(handler: object, key: str = "fixture-key") -> genai.Client:
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            base_url="https://vendor.example.test",
            httpx_async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                trust_env=False,
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
async def test_native_tool_history_signature_usage_and_close(
    vendor: str,
) -> None:
    bodies: list[dict[str, object]] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(incoming.content))
        body = anthropic_body if vendor == "anthropic" else google_body
        return httpx.Response(200, json=body(len(bodies) == 1))

    source = request()
    adapter = chat_anthropic if vendor == "anthropic" else chat_google
    client = (
        anthropic_client(handler)
        if vendor == "anthropic"
        else google_client(handler)
    )
    first = await adapter.complete(source, client)
    message = first.choices[0].message
    assert first.choices[0].finish_reason == "tool_calls"
    assert first.usage.model_dump() == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert message.content != "private"
    assert message.tool_calls[0].function.arguments == '{"number":1}'
    assert message.provider_state.provider == vendor
    source.messages.extend(
        [
            ChatMessage(
                role="assistant", **message.model_dump(exclude={"role"})
            ),
            ChatMessage(
                role="tool",
                tool_call_id=message.tool_calls[0].id,
                content="<error>fixture-failure</error>",
            ),
        ]
    )
    second_client = (
        anthropic_client(handler)
        if vendor == "anthropic"
        else google_client(handler)
    )
    second = await adapter.complete(source, second_client)
    assert second.choices[0].message.content == "已查询"
    if vendor == "anthropic":
        history = bodies[1]["messages"]
        assert history[-2]["content"][0]["signature"] == "sig"
        assert history[-1]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "call-1",
            "content": "<error>fixture-failure</error>",
            "is_error": True,
        }
        assert client.is_closed() and second_client.is_closed()
        assert bodies[0]["tool_choice"] == {"type": "any"}
    else:
        history = bodies[1]["contents"]
        assert history[-2]["parts"][0]["thoughtSignature"] == "c2ln"
        assert history[-1]["parts"][0]["functionResponse"]["name"] == "query"
        # google-genai 1.49.0 serializes this nested option using snake_case.
        assert (
            bodies[0]["toolConfig"]["function_calling_config"]["mode"] == "ANY"
        )
        assert client._api_client._async_httpx_client.is_closed
        assert second_client._api_client._async_httpx_client.is_closed
        assert bodies[0]["systemInstruction"]["parts"] == [{"text": "企业规则"}]
    assert len(bodies) == 2


def anthropic_events(terminal: bool = True) -> str:
    events = [
        (
            "message_start",
            {
                "message": {
                    **anthropic_body(),
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 11, "output_tokens": 0},
                }
            },
        ),
        (
            "content_block_start",
            {
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "query",
                    "input": {},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"number":',
                },
            },
        ),
        (
            "content_block_delta",
            {
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": "1}",
                },
            },
        ),
        ("content_block_stop", {"index": 0}),
        (
            "message_delta",
            {
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 7},
            },
        ),
    ]
    if terminal:
        events.append(("message_stop", {}))
    return "".join(
        f"event: {kind}\ndata: {json.dumps({'type': kind, **body})}\n\n"
        for kind, body in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
async def test_native_stream_reassembles_calls_and_roundtrips_context(
    vendor: str,
) -> None:
    def handler(incoming: httpx.Request) -> httpx.Response:
        body = (
            anthropic_events()
            if vendor == "anthropic"
            else ("data: " + json.dumps(google_body()) + "\n\n")
        )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    adapter = chat_anthropic if vendor == "anthropic" else chat_google
    client = (
        anthropic_client(handler)
        if vendor == "anthropic"
        else google_client(handler)
    )
    chunks = [chunk async for chunk in adapter.stream(request(), client)]
    observer = ToolCallStream()
    for chunk in chunks:
        observer.observe(chunk)
    calls = observer.finish()
    assert len(calls) == 1 and json.loads(calls[0].arguments) == {"number": 1}
    assert observer.provider_state.provider == vendor
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
async def test_missing_native_terminal_never_looks_complete(
    vendor: str,
) -> None:
    def handler(incoming: httpx.Request) -> httpx.Response:
        body = google_body()
        del body["candidates"][0]["finishReason"]
        raw = (
            anthropic_events(False)
            if vendor == "anthropic"
            else ("data: " + json.dumps(body) + "\n\n")
        )
        return httpx.Response(
            200, text=raw, headers={"content-type": "text/event-stream"}
        )

    adapter = chat_anthropic if vendor == "anthropic" else chat_google
    client = (
        anthropic_client(handler)
        if vendor == "anthropic"
        else google_client(handler)
    )
    output: list[str] = []
    with pytest.raises(ValueError):
        async for chunk in adapter.stream(request(), client):
            output.append(chunk)
    assert "data: [DONE]\n\n" not in output


@pytest.mark.asyncio
async def test_google_keys_stay_scoped_during_concurrent_calls() -> None:
    keys: list[str] = []
    ready = asyncio.Event()

    async def handler(incoming: httpx.Request) -> httpx.Response:
        keys.append(incoming.headers["x-goog-api-key"])
        if len(keys) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return httpx.Response(200, json=google_body(False))

    await asyncio.gather(
        *[
            chat_google.complete(request(), google_client(handler, key))
            for key in ("fixture-a", "fixture-b")
        ]
    )
    assert set(keys) == {"fixture-a", "fixture-b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
@pytest.mark.parametrize(
    "change", ["text", "arguments", "identifier", "missing_result", "orphan"]
)
async def test_mismatched_history_is_rejected_before_network(
    vendor: str, change: str
) -> None:
    adapter = chat_anthropic if vendor == "anthropic" else chat_google
    sent = 0

    def handler(incoming: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(
            200,
            json=anthropic_body() if vendor == "anthropic" else google_body(),
        )

    make_client = anthropic_client if vendor == "anthropic" else google_client
    source = request()
    response = await adapter.complete(source, make_client(handler))
    assistant = ChatMessage(
        role="assistant",
        **response.choices[0].message.model_dump(exclude={"role"}),
    )
    result = ChatMessage(
        role="tool", content="result", tool_call_id=assistant.tool_calls[0].id
    )
    if change == "text":
        assistant.content = "altered"
    elif change == "arguments":
        assistant.tool_calls[0].function.arguments = '{"number":2}'
    elif change == "identifier":
        assistant.tool_calls[0].id = result.tool_call_id = "altered"
    source.messages.extend(
        [assistant, result] if change != "missing_result" else [assistant]
    )
    if change == "orphan":
        source.messages.append(result.model_copy())
    with pytest.raises(ValueError):
        await adapter.complete(source, make_client(handler))
    assert sent == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
async def test_transport_error_closes_client(vendor: str) -> None:
    def handler(incoming: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fixture transport unavailable")

    adapter = chat_anthropic if vendor == "anthropic" else chat_google
    client = (
        anthropic_client(handler)
        if vendor == "anthropic"
        else google_client(handler)
    )
    with pytest.raises(Exception):
        await adapter.complete(request(), client)
    assert (
        client.is_closed()
        if vendor == "anthropic"
        else client._api_client._async_httpx_client.is_closed
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
async def test_cancelled_request_closes_client(vendor: str) -> None:
    entered = asyncio.Event()

    async def handler(incoming: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    adapter = chat_anthropic if vendor == "anthropic" else chat_google
    client = (
        anthropic_client(handler)
        if vendor == "anthropic"
        else google_client(handler)
    )
    running = asyncio.create_task(adapter.complete(request(), client))
    await asyncio.wait_for(entered.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert (
        client.is_closed()
        if vendor == "anthropic"
        else client._api_client._async_httpx_client.is_closed
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["anthropic", "google"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_service_uses_native_history_for_second_round(
    vendor: str,
    streaming: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, object]] = []
    clients: list[AsyncAnthropic | genai.Client] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(incoming.content))
        first = len(bodies) == 1
        if not streaming:
            body = (
                anthropic_body(first)
                if vendor == "anthropic"
                else google_body(first)
            )
            return httpx.Response(200, json=body)
        if vendor == "google":
            raw = "data: " + json.dumps(google_body(first)) + "\n\n"
        elif first:
            raw = anthropic_events()
        else:
            events = [
                (
                    "message_start",
                    {
                        "message": {
                            **anthropic_body(False),
                            "content": [],
                            "stop_reason": None,
                        }
                    },
                ),
                (
                    "content_block_start",
                    {
                        "index": 0,
                        "content_block": {"type": "text", "text": "已查询"},
                    },
                ),
                ("content_block_stop", {"index": 0}),
                (
                    "message_delta",
                    {
                        "delta": {
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                        },
                        "usage": {"output_tokens": 7},
                    },
                ),
                ("message_stop", {}),
            ]
            raw = "".join(
                f"event: {kind}\ndata: "
                + json.dumps({"type": kind, **body}) + "\n\n"
                for kind, body in events
            )
        return httpx.Response(
            200, text=raw, headers={"content-type": "text/event-stream"}
        )

    def client_factory(provider: object) -> AsyncAnthropic | genai.Client:
        client = (
            anthropic_client(handler)
            if vendor == "anthropic"
            else google_client(handler)
        )
        clients.append(client)
        return client

    source = request()
    source.auto_execute_tools = True
    source.max_tool_rounds = 1
    executor = SimpleNamespace(
        set_context=Mock(),
        register_tools=AsyncMock(),
        execute=AsyncMock(return_value="fixture-result"),
    )
    monkeypatch.setattr(
        "app.services.chat_service.ToolExecutor", Mock(return_value=executor)
    )
    service = ChatService(cast(AsyncSession, object()))
    monkeypatch.setattr(
        service,
        "_get_provider",
        AsyncMock(
            return_value=SimpleNamespace(
                provider_kind=vendor, project_id=uuid4()
            )
        ),
    )
    monkeypatch.setattr(
        service, "_merge_tools", AsyncMock(return_value=source.tools)
    )
    monkeypatch.setattr(service, f"_create_{vendor}_client", client_factory)
    token = metered_execution.set(False)
    try:
        if streaming:
            output = [
                item
                async for item in service.create_completion_stream(
                    source, uuid4()
                )
            ]
            assert not any('"error"' in item for item in output)
            assert output.count("data: [DONE]\n\n") == 1
            assert any("已查询" in item for item in output)
        else:
            output = await service.create_completion(source, uuid4())
            assert output.choices[0].message.content == "已查询"
            assert output.usage.total_tokens == 36
    finally:
        metered_execution.reset(token)
    executor.execute.assert_awaited_once_with("query", '{"number":1}')
    assert len(bodies) == len(clients) == 2
    assert source.messages[-2].provider_state is not None
    assert source.tools is None and source.tool_choice is None
    assert all(
        client.is_closed()
        if vendor == "anthropic"
        else client._api_client._async_httpx_client.is_closed
        for client in clients
    )


@pytest.mark.parametrize("vendor", ["anthropic", "google"])
def test_missing_key_never_uses_environment_credentials(
    vendor: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unrelated-fixture")
    monkeypatch.setenv("GOOGLE_API_KEY", "unrelated-fixture")
    monkeypatch.setenv("GEMINI_API_KEY", "unrelated-fixture")
    service = ChatService(cast(AsyncSession, object()))
    with pytest.raises(ChatCompletionError, match="未配置 API 密钥"):
        getattr(service, f"_create_{vendor}_client")(
            SimpleNamespace(api_key=None)
        )
