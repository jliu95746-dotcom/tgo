"""Only complete, well-formed model batches may enter tool execution."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import AsyncIterator, Iterator, Literal, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.chat import (
    ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Choice,
    ChoiceMessage, FunctionCall, ToolCall,
)
from app.services.chat_service import ChatCompletionError, ChatService
from app.services.quota_authorization import metered_execution


@pytest.fixture
def service_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ChatService, SimpleNamespace]]:
    token = metered_execution.set(False)
    executor = SimpleNamespace(
        set_context=Mock(), register_tools=AsyncMock(),
        execute=AsyncMock(return_value="synthetic-tool-result"),
    )
    monkeypatch.setattr(
        "app.services.chat_service.ToolExecutor", Mock(return_value=executor),
    )
    service = ChatService(cast(AsyncSession, object()))
    monkeypatch.setattr(service, "_get_provider", AsyncMock(
        return_value=SimpleNamespace(provider_kind="openai_compatible"),
    ))
    monkeypatch.setattr(service, "_merge_tools", AsyncMock(return_value=[]))
    try:
        yield service, executor
    finally:
        metered_execution.reset(token)


def request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        provider_id=uuid4(), model="synthetic-model",
        messages=[ChatMessage(role="user", content="查询本人测试订单")],
        auto_execute_tools=True, max_tool_rounds=2,
    )


def call(**overrides: object) -> ToolCall:
    values: dict[str, object] = {
        "id": "call-1", "type": "function",
        "function": FunctionCall(
            name="query", arguments='{"order":"fixture-private-order"}',
        ),
    }
    values.update(overrides)
    return ToolCall.model_validate(values)


def completion(calls: list[ToolCall] | None = None) -> ChatCompletionResponse:
    return ChatCompletionResponse(model="synthetic-model", choices=[Choice(
        index=0, message=ChoiceMessage(content="完成", tool_calls=calls),
        finish_reason="tool_calls" if calls else "stop",
    )])


def chunk(
    delta: object = None, finish: str | None = None, choice: int = 0,
) -> str:
    return "data: " + json.dumps({
        "object": "chat.completion.chunk", "choices": [{
            "index": choice, "delta": {} if delta is None else delta,
            "finish_reason": finish,
        }],
    }) + "\n\n"


def invalid_call(kind: str) -> ToolCall:
    if kind == "function":
        return call(function=None)
    if kind == "id":
        return call(id=None)
    if kind == "blank_id":
        return call(id=" ")
    if kind in {"name", "blank_name"}:
        return call(function=FunctionCall(
            name=None if kind == "name" else " ", arguments="{}",
        ))
    arguments = {
        "arguments": None, "empty": "", "json": '{"private":',
        "array": "[]", "null": "null", "scalar": "0",
        "nonfinite": '{"value": NaN}',
        "overflow": '{"value": 1e999}',
        "duplicate_keys": '{"value": 1, "value": 2}',
    }[kind]
    return call(function=FunctionCall(name="query", arguments=arguments))


INVALID = [
    "function", "id", "blank_id", "name", "blank_name", "arguments",
    "empty", "json", "array", "null", "scalar", "nonfinite",
    "duplicate_keys", "duplicate_ids", "overflow",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", INVALID)
async def test_invalid_batch_never_executes_even_its_valid_member(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    service, executor = service_and_executor
    calls = [call(id="valid-first"), invalid_call(kind)] if (
        kind != "duplicate_ids"
    ) else [call(), call()]
    monkeypatch.setattr(service, "_openai_completion", AsyncMock(
        side_effect=[completion(calls), completion()],
    ))
    source = request()
    with pytest.raises(ChatCompletionError) as caught:
        await service.create_completion(source, uuid4())
    executor.execute.assert_not_called()
    assert len(source.messages) == 1
    assert "fixture-private-order" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "stop", "length", "content_filter"])
async def test_nonstream_tool_batch_requires_model_completion(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    reason: Literal["stop", "length", "content_filter"] | None,
) -> None:
    service, executor = service_and_executor
    output = completion([call()])
    output.choices[0].finish_reason = reason
    monkeypatch.setattr(service, "_openai_completion", AsyncMock(
        side_effect=[output, completion()],
    ))
    with pytest.raises(ChatCompletionError):
        await service.create_completion(request(), uuid4())
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_valid_nonstream_batch_preserves_correlated_history(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, executor = service_and_executor
    monkeypatch.setattr(service, "_openai_completion", AsyncMock(
        side_effect=[completion([call(), call(id="call-2")]), completion()],
    ))
    source = request()
    result = await service.create_completion(source, uuid4())
    assert result.choices[0].message.content == "完成"
    assert executor.execute.await_count == 2
    assert [message.tool_call_id for message in source.messages[2:]] == [
        "call-1", "call-2",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", INVALID)
async def test_invalid_stream_batch_ends_with_one_safe_error(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    service, executor = service_and_executor
    calls = [call(id="valid-first"), invalid_call(kind)] if (
        kind != "duplicate_ids"
    ) else [call(), call()]
    deltas = [item.model_dump() | {"index": index}
              for index, item in enumerate(calls)]

    async def model_stream(*args: object) -> AsyncIterator[str]:
        yield chunk({"tool_calls": deltas})
        yield chunk(finish="tool_calls")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(service, "_openai_stream", model_stream)
    source = request()
    events = [part async for part in service.create_completion_stream(
        source, uuid4(),
    )]
    payloads = [json.loads(part[6:]) for part in events
                if part.startswith("data: {")]
    errors = [item for item in payloads if "error" in item]
    assert len(errors) == 1
    assert "fixture-private-order" not in json.dumps(errors)
    assert not any(item.get("type") == "tool_call" for item in payloads)
    assert events[-1] == "data: [DONE]\n\n"
    assert events.count("data: [DONE]\n\n") == 1
    executor.execute.assert_not_called()
    assert len(source.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [
    "eof", "done_only", "finish_only", "length", "content_filter",
    "stop", "api_error", "malformed", "after_finish", "changed_id",
    "exception",
])
async def test_incomplete_or_failed_stream_cannot_execute(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch, ending: str,
) -> None:
    service, executor = service_and_executor

    async def model_stream(*args: object) -> AsyncIterator[str]:
        yield chunk({"tool_calls": [call().model_dump() | {"index": 0}]})
        if ending == "eof":
            return
        if ending == "done_only":
            yield "data: [DONE]\n\n"
            return
        if ending in {"length", "content_filter", "stop"}:
            yield chunk(finish=ending)
        elif ending == "api_error":
            yield 'data: {"error":{"message":"provider unavailable"}}\n\n'
        elif ending == "malformed":
            yield "data: {invalid}\n\n"
        elif ending == "exception":
            raise RuntimeError("fixture-private-order")
        elif ending == "changed_id":
            yield chunk({"tool_calls": [{"index": 0, "id": "other"}]})
            yield chunk(finish="tool_calls")
        else:
            yield chunk(finish="tool_calls")
        if ending == "after_finish":
            yield chunk({"tool_calls": [{"index": 0, "function": {
                "arguments": "{}",
            }}]})
        if ending != "finish_only":
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(service, "_openai_stream", model_stream)
    events = [part async for part in service.create_completion_stream(
        request(), uuid4(),
    )]
    executor.execute.assert_not_called()
    assert sum('"error"' in part for part in events) == 1
    assert events[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_fragmented_stream_ignores_other_choice_and_executes_once(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, executor = service_and_executor
    rounds = 0

    async def model_stream(*args: object) -> AsyncIterator[str]:
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield chunk({"tool_calls": [call(id="other-choice").model_dump()
                                        | {"index": 0}]}, choice=1)
            yield chunk({"tool_calls": [{
                "index": 0, "id": "call-1", "type": "function",
                "function": {"name": "que", "arguments": '{"order":'},
            }]})
            yield chunk({"tool_calls": [{
                "index": 0, "id": "call-1",
                "function": {"name": "ry", "arguments": '"fixture"}'},
            }]})
            yield chunk(finish="tool_calls")
        else:
            yield chunk({"content": "完成"}, finish="stop")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(service, "_openai_stream", model_stream)
    events = [part async for part in service.create_completion_stream(
        request(), uuid4(),
    )]
    executor.execute.assert_awaited_once_with("query", '{"order":"fixture"}')
    assert rounds == 2
    assert events.count("data: [DONE]\n\n") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_raw_proxy_does_not_execute_or_rewrite_provider_output(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch, stream: bool,
) -> None:
    service, executor = service_and_executor
    source = request()
    source.auto_execute_tools = False
    if stream:
        raw = ["data: {not-json}\n\n", "data: [DONE]\n\n"]

        async def model_stream(*args: object) -> AsyncIterator[str]:
            for part in raw:
                yield part

        monkeypatch.setattr(service, "_openai_stream", model_stream)
        assert [part async for part in service.create_completion_stream(
            source, uuid4(),
        )] == raw
    else:
        output = completion([call(function=None)])
        monkeypatch.setattr(service, "_openai_completion", AsyncMock(
            return_value=output,
        ))
        assert await service.create_completion(source, uuid4()) is output
    executor.register_tools.assert_not_awaited()
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_stream_cancellation_propagates_without_execution(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, executor = service_and_executor

    async def model_stream(*args: object) -> AsyncIterator[str]:
        yield chunk({"tool_calls": [call().model_dump() | {"index": 0}]})
        raise asyncio.CancelledError()

    monkeypatch.setattr(service, "_openai_stream", model_stream)
    with pytest.raises(asyncio.CancelledError):
        _ = [part async for part in service.create_completion_stream(
            request(), uuid4(),
        )]
    executor.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [
    {"tool_calls": [{"index": None}]},
    {"tool_calls": [{"index": -1}]},
    {"tool_calls": [{"index": True}]},
    {"tool_calls": [{"index": "0"}]},
    {"tool_calls": [None]},
    {"tool_calls": "not-a-list"},
    {"tool_calls": [{"index": 0, "function": []}]},
    {"tool_calls": [{"index": 0, "function": {"arguments": 1}}]},
    {"tool_calls": [{"index": 0, "id": 1}]},
    {"tool_calls": [{"index": 0, "type": {}}]},
])
async def test_invalid_delta_shape_never_reaches_execution(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch, delta: dict[str, object],
) -> None:
    service, executor = service_and_executor

    async def model_stream(*args: object) -> AsyncIterator[str]:
        yield chunk(delta)
        yield chunk(finish="tool_calls")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(service, "_openai_stream", model_stream)
    events = [part async for part in service.create_completion_stream(
        request(), uuid4(),
    )]
    assert sum('"error"' in part for part in events) == 1
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_later_bad_batch_keeps_prior_results_and_does_not_repeat_tools(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, executor = service_and_executor
    monkeypatch.setattr(service, "_openai_completion", AsyncMock(
        side_effect=[completion([call()]), completion([call(function=None)])],
    ))
    source = request()
    with pytest.raises(ChatCompletionError, match="本轮工具未执行"):
        await service.create_completion(source, uuid4())
    executor.execute.assert_awaited_once()
    assert source.messages[-1].role == "tool"
    assert source.messages[-1].content == "synthetic-tool-result"


@pytest.mark.asyncio
async def test_nonstream_selects_primary_choice_without_executing_other_choice(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, executor = service_and_executor
    output = completion()
    output.choices.insert(0, Choice(
        index=1, message=ChoiceMessage(tool_calls=[call()]),
        finish_reason="tool_calls",
    ))
    monkeypatch.setattr(service, "_openai_completion", AsyncMock(
        side_effect=[output, completion()],
    ))
    assert await service.create_completion(request(), uuid4()) is output
    executor.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("selection", ["none", "named"])
async def test_selection_cannot_execute_a_different_function(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
    selection: str,
) -> None:
    from app.schemas.chat import FunctionDefinition, ToolDefinition

    service, executor = service_and_executor
    source = request()
    source.tools = [
        ToolDefinition(function=FunctionDefinition(name="allowed"))
    ]
    monkeypatch.setattr(
        service, "_merge_tools", AsyncMock(return_value=source.tools)
    )
    source.tool_choice = (
        "none"
        if selection == "none"
        else {
            "type": "function",
            "function": {"name": "allowed"},
        }
    )
    if stream:

        async def model_stream(*args: object) -> AsyncIterator[str]:
            yield chunk({"tool_calls": [call().model_dump() | {"index": 0}]})
            yield chunk(finish="tool_calls")
            yield "data: [DONE]\n\n"

        monkeypatch.setattr(service, "_openai_stream", model_stream)
        output = [
            item
            async for item in service.create_completion_stream(source, uuid4())
        ]
        assert any('"error"' in item for item in output)
    else:
        monkeypatch.setattr(
            service,
            "_openai_completion",
            AsyncMock(return_value=completion([call()])),
        )
        with pytest.raises(ChatCompletionError):
            await service.create_completion(source, uuid4())
    executor.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_round_limit_clears_tool_choice_and_rejects_more_calls(
    service_and_executor: tuple[ChatService, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
) -> None:
    service, executor = service_and_executor
    source = request()
    source.max_tool_rounds = 1
    source.tool_choice = "required"
    rounds = 0

    def next_output() -> ChatCompletionResponse:
        nonlocal rounds
        rounds += 1
        if rounds == 2:
            assert source.tools is None and source.tool_choice is None
        return completion([call()])

    if stream:

        async def model_stream(*args: object) -> AsyncIterator[str]:
            next_output()
            yield chunk({"tool_calls": [call().model_dump() | {"index": 0}]})
            yield chunk(finish="tool_calls")
            yield "data: [DONE]\n\n"

        monkeypatch.setattr(service, "_openai_stream", model_stream)
        output = [
            item
            async for item in service.create_completion_stream(source, uuid4())
        ]
        assert sum('"error"' in item for item in output) == 1
        assert output.count("data: [DONE]\n\n") == 1
    else:
        monkeypatch.setattr(
            service,
            "_openai_completion",
            AsyncMock(side_effect=lambda *args: next_output()),
        )
        with pytest.raises(ChatCompletionError):
            await service.create_completion(source, uuid4())
    assert rounds == 2
    executor.execute.assert_awaited_once()


def test_text_stream_requires_a_terminal_reason() -> None:
    from app.services.chat_tool_calls import (
        InvalidToolCallError,
        ToolCallStream,
    )

    observed = ToolCallStream()
    observed.observe(chunk({"content": "partial"}))
    observed.observe("data: [DONE]\n\n")
    with pytest.raises(InvalidToolCallError):
        observed.finish()
