"""Anthropic text/tool protocol using the installed async SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import NotRequired, TypedDict

from anthropic import AsyncAnthropic
from anthropic.types import (
    ContentBlockParam,
    Message,
    MessageParam,
    ToolChoiceParam,
    ToolParam,
)

from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    FunctionCall,
    ToolCall,
    Usage,
)
from app.schemas.chat_provider_state import ProviderMessageState
from app.services.chat_accounting import observe_stream_usage
from app.services.chat_provider_protocol import (
    FinishReason,
    Frames,
    ToolExchange,
    arguments,
    encode_arguments,
    history,
    tool_policy,
    verify_projection,
)
from app.services.chat_tool_calls import InvalidToolCallError


class Parameters(TypedDict):
    model: str
    max_tokens: int
    messages: list[MessageParam]
    system: NotRequired[str]
    temperature: NotRequired[float]
    top_p: NotRequired[float]
    stop_sequences: NotRequired[list[str]]
    tools: NotRequired[list[ToolParam]]
    tool_choice: NotRequired[ToolChoiceParam]


def _parts(response: Message) -> list[ContentBlockParam]:
    parts: list[ContentBlockParam] = []
    for block in response.content:
        if block.type == "text":
            parts.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            parts.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        elif block.type == "thinking":
            parts.append(
                {
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                }
            )
        elif block.type == "redacted_thinking":
            parts.append({"type": "redacted_thinking", "data": block.data})
        else:
            raise InvalidToolCallError()
    return parts


def _message(response: Message) -> ChoiceMessage:
    _parts(
        response
    )  # Reject unsupported native actions instead of losing them.
    calls = [
        ToolCall(
            id=block.id,
            function=FunctionCall(
                name=block.name,
                arguments=encode_arguments(block.input),
            ),
        )
        for block in response.content
        if block.type == "tool_use"
    ]
    state = ProviderMessageState(
        provider="anthropic",
        payload=response.model_dump_json(),
        call_ids=tuple(call.id for call in calls if call.id is not None),
    )
    return ChoiceMessage(
        content="".join(
            block.text for block in response.content if block.type == "text"
        ),
        tool_calls=calls or None,
        provider_state=state,
    )


def _assistant(message: ChatMessage) -> MessageParam:
    state = message.provider_state
    if state is not None and state.provider == "anthropic":
        native = Message.model_validate_json(state.payload)
        projected = _message(native)
        if (
            projected.provider_state is None
            or state.call_ids != projected.provider_state.call_ids
        ):
            raise InvalidToolCallError()
        verify_projection(
            message, projected.content or "", projected.tool_calls or []
        )
        return {"role": "assistant", "content": _parts(native)}
    parts: list[ContentBlockParam] = []
    if message.content:
        parts.append({"type": "text", "text": message.content})
    for call in message.tool_calls or []:
        if (
            call.id is None
            or call.function is None
            or call.function.name is None
        ):
            raise InvalidToolCallError()
        parts.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.function.name,
                "input": arguments(call.function.arguments or ""),
            }
        )
    return {"role": "assistant", "content": parts or ""}


def parameters(request: ChatCompletionRequest) -> Parameters:
    if request.n not in (None, 1):
        raise ValueError("Anthropic does not support n > 1")
    system, entries = history(request.messages)
    messages: list[MessageParam] = []
    for entry in entries:
        if isinstance(entry, ToolExchange):
            messages.append(_assistant(entry.assistant))
            blocks: list[ContentBlockParam] = []
            for call, result in zip(entry.calls, entry.results):
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.identifier,
                        "content": result.content or "",
                        "is_error": (result.content or "").startswith(
                            "<error>"
                        ),
                    }
                )
            messages.append({"role": "user", "content": blocks})
        elif entry.role == "assistant":
            messages.append(_assistant(entry))
        else:
            messages.append({"role": "user", "content": entry.content or ""})
    params: Parameters = {
        "model": request.model,
        "max_tokens": request.max_tokens or 4096,
        "messages": messages,
    }
    if system:
        params["system"] = system
    if request.temperature is not None:
        params["temperature"] = request.temperature
    if request.top_p is not None:
        params["top_p"] = request.top_p
    if request.stop:
        params["stop_sequences"] = (
            request.stop if isinstance(request.stop, list) else [request.stop]
        )
    policy = tool_policy(request)
    if request.tools:
        params["tools"] = [
            ToolParam(
                name=tool.function.name,
                description=tool.function.description or "",
                input_schema=tool.function.parameters
                or {"type": "object", "properties": {}},
            )
            for tool in request.tools
        ]
        if policy.mode == "named":
            if policy.name is None:
                raise InvalidToolCallError()
            params["tool_choice"] = {"type": "tool", "name": policy.name}
        elif policy.mode == "required":
            params["tool_choice"] = {"type": "any"}
        elif policy.mode == "none":
            params["tool_choice"] = {"type": "none"}
        else:
            params["tool_choice"] = {"type": "auto"}
    elif policy.mode not in ("auto", "none"):
        raise InvalidToolCallError()
    return params


def _finish(response: Message) -> FinishReason:
    reason = response.stop_reason
    if reason in ("end_turn", "stop_sequence"):
        return "stop"
    if reason == "tool_use":
        return "tool_calls"
    if reason in ("max_tokens", "model_context_window_exceeded"):
        return "length"
    if reason == "refusal":
        return "content_filter"
    raise InvalidToolCallError()


def _usage(response: Message) -> Usage:
    measured = response.usage
    prompt = (
        measured.input_tokens
        + (measured.cache_read_input_tokens or 0)
        + (measured.cache_creation_input_tokens or 0)
    )
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=measured.output_tokens,
        total_tokens=prompt + measured.output_tokens,
    )


async def complete(
    request: ChatCompletionRequest,
    client: AsyncAnthropic,
) -> ChatCompletionResponse:
    async with client:
        response = await client.messages.create(**parameters(request))
        return ChatCompletionResponse(
            model=response.model,
            choices=[
                Choice(
                    index=0,
                    message=_message(response),
                    finish_reason=_finish(response),
                )
            ],
            usage=_usage(response),
        )


async def stream(
    request: ChatCompletionRequest,
    client: AsyncAnthropic,
) -> AsyncIterator[str]:
    frames = Frames(request.model)
    tool_indices: dict[int, int] = {}
    initial: dict[int, str] = {}
    had_delta: set[int] = set()
    stopped = False
    async with client:
        async with client.messages.stream(**parameters(request)) as native:
            yield frames.chunk(DeltaMessage(role="assistant"))
            async for event in native:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_indices[event.index] = len(tool_indices)
                        initial[event.index] = encode_arguments(block.input)
                        yield frames.chunk(
                            DeltaMessage(
                                tool_calls=[
                                    ToolCall(
                                        index=tool_indices[event.index],
                                        id=block.id,
                                        function=FunctionCall(
                                            name=block.name, arguments=""
                                        ),
                                    )
                                ]
                            )
                        )
                    elif block.type == "text" and block.text:
                        yield frames.chunk(DeltaMessage(content=block.text))
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield frames.chunk(DeltaMessage(content=delta.text))
                    elif delta.type == "input_json_delta":
                        if (
                            event.index not in tool_indices
                            or initial[event.index] != "{}"
                        ):
                            raise InvalidToolCallError()
                        had_delta.add(event.index)
                        yield frames.chunk(
                            DeltaMessage(
                                tool_calls=[
                                    ToolCall(
                                        index=tool_indices[event.index],
                                        type=None,
                                        function=FunctionCall(
                                            arguments=delta.partial_json
                                        ),
                                    )
                                ]
                            )
                        )
                elif (
                    event.type == "content_block_stop"
                    and event.index in tool_indices
                ):
                    if event.index not in had_delta:
                        yield frames.chunk(
                            DeltaMessage(
                                tool_calls=[
                                    ToolCall(
                                        index=tool_indices[event.index],
                                        type=None,
                                        function=FunctionCall(
                                            arguments=initial[event.index]
                                        ),
                                    )
                                ]
                            )
                        )
                elif event.type == "message_stop":
                    stopped = True
            if not stopped:
                raise InvalidToolCallError()
            response = await native.get_final_message()
            message = _message(response)
            observe_stream_usage(_usage(response))
            yield frames.chunk(
                DeltaMessage(provider_state=message.provider_state),
                _finish(response),
            )
    yield "data: [DONE]\n\n"
