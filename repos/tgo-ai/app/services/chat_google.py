"""Request-scoped Google GenerateContent text/tool adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from google import genai
from google.genai import types
from pydantic import TypeAdapter

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

_contents = TypeAdapter(list[types.Content])


def _projection(
    contents: Sequence[types.Content],
    identifiers: tuple[str, ...] | None = None,
) -> tuple[str, list[ToolCall]]:
    text: list[str] = []
    calls: list[ToolCall] = []
    for content in contents:
        if content.role != "model":
            raise InvalidToolCallError()
        for part in content.parts or []:
            if set(part.model_dump(exclude_none=True)) - {
                "text",
                "thought",
                "thought_signature",
                "function_call",
            }:
                raise InvalidToolCallError()
            if part.text is not None and not part.thought:
                text.append(part.text)
            native = part.function_call
            if native is not None:
                index = len(calls)
                if not native.name or (
                    identifiers is not None and index >= len(identifiers)
                ):
                    raise InvalidToolCallError()
                identifier = (
                    identifiers[index]
                    if identifiers is not None
                    else (native.id or f"call_{uuid4().hex}")
                )
                if native.id and identifier != native.id:
                    raise InvalidToolCallError()
                calls.append(
                    ToolCall(
                        id=identifier,
                        function=FunctionCall(
                            name=native.name,
                            arguments=encode_arguments(native.args or {}),
                        ),
                    )
                )
    if identifiers is not None and len(identifiers) != len(calls):
        raise InvalidToolCallError()
    return "".join(text), calls


def _message(
    contents: list[types.Content], calls: list[ToolCall] | None = None
) -> ChoiceMessage:
    identifiers = (
        None if calls is None else tuple(call.id or "" for call in calls)
    )
    text, projected = _projection(contents, identifiers)
    return ChoiceMessage(
        content=text,
        tool_calls=projected or None,
        provider_state=ProviderMessageState(
            provider="google",
            call_ids=tuple(call.id or "" for call in projected),
            payload=_contents.dump_json(contents, exclude_none=True).decode(),
        ),
    )


def _assistant(message: ChatMessage) -> list[types.Content]:
    state = message.provider_state
    if state is not None and state.provider == "google":
        native = _contents.validate_json(state.payload)
        text, calls = _projection(native, state.call_ids)
        verify_projection(message, text, calls)
        return native
    parts: list[types.Part] = []
    if message.content:
        parts.append(types.Part(text=message.content))
    for call in message.tool_calls or []:
        if call.function is None or call.function.name is None:
            raise InvalidToolCallError()
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=call.id,
                    name=call.function.name,
                    args=arguments(call.function.arguments or ""),
                )
            )
        )
    return [types.Content(role="model", parts=parts)]


def parameters(
    request: ChatCompletionRequest,
) -> tuple[list[types.Content], types.GenerateContentConfig]:
    system, entries = history(request.messages)
    contents: list[types.Content] = []
    for entry in entries:
        if isinstance(entry, ToolExchange):
            native = _assistant(entry.assistant)
            contents.extend(native)
            function_calls = [
                part.function_call
                for content in native
                for part in content.parts or []
                if part.function_call is not None
            ]
            parts: list[types.Part] = []
            for call, result, function_call in zip(
                entry.calls, entry.results, function_calls
            ):
                error = (result.content or "").startswith("<error>")
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=function_call.id,
                            name=call.name,
                            response={
                                "error"
                                if error
                                else "result": result.content or ""
                            },
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=parts))
        elif entry.role == "assistant":
            contents.extend(_assistant(entry))
        else:
            contents.append(
                types.Content(
                    role="user", parts=[types.Part(text=entry.content or "")]
                )
            )
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=request.temperature,
        top_p=request.top_p,
        max_output_tokens=request.max_tokens,
        candidate_count=request.n,
        seed=request.seed,
        presence_penalty=request.presence_penalty,
        frequency_penalty=request.frequency_penalty,
        stop_sequences=(
            request.stop if isinstance(request.stop, list) else [request.stop]
        )
        if request.stop
        else None,
        response_mime_type="application/json"
        if request.response_format is not None
        and request.response_format.type == "json_object"
        else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    )
    policy = tool_policy(request)
    if request.tools:
        config.tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=tool.function.name,
                        description=tool.function.description,
                        parameters_json_schema=tool.function.parameters
                        or {"type": "object", "properties": {}},
                    )
                    for tool in request.tools
                ]
            )
        ]
        mode = {
            "auto": "AUTO",
            "none": "NONE",
            "required": "ANY",
            "named": "ANY",
        }[policy.mode]
        config.tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode(mode),
                allowed_function_names=[policy.name]
                if policy.name is not None
                else None,
            )
        )
    elif policy.mode not in ("auto", "none"):
        raise InvalidToolCallError()
    return contents, config


def _finish(
    reason: types.FinishReason | None, has_calls: bool
) -> FinishReason:
    if reason == types.FinishReason.STOP:
        return "tool_calls" if has_calls else "stop"
    if reason == types.FinishReason.MAX_TOKENS:
        return "length"
    if reason in {
        types.FinishReason.SAFETY,
        types.FinishReason.RECITATION,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.SPII,
        types.FinishReason.IMAGE_SAFETY,
    }:
        return "content_filter"
    raise InvalidToolCallError()


def _usage(response: types.GenerateContentResponse) -> Usage | None:
    measured = response.usage_metadata
    if measured is None:
        return None
    prompt, total = measured.prompt_token_count, measured.total_token_count
    if (
        type(prompt) is not int
        or type(total) is not int
        or prompt < 0
        or total < prompt
    ):
        return None
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=total - prompt,
        total_tokens=total,
    )


async def complete(
    request: ChatCompletionRequest, client: genai.Client
) -> ChatCompletionResponse:
    try:
        async with client.aio as aio:
            contents, config = parameters(request)
            native = await aio.models.generate_content(
                model=request.model, contents=contents, config=config
            )
            choices: list[Choice] = []
            for candidate in native.candidates or []:
                message = _message(
                    [
                        candidate.content
                        or types.Content(role="model", parts=[])
                    ]
                )
                choices.append(
                    Choice(
                        index=candidate.index or 0,
                        message=message,
                        finish_reason=_finish(
                            candidate.finish_reason, bool(message.tool_calls)
                        ),
                    )
                )
            if not choices:
                raise InvalidToolCallError()
            if sorted(choice.index for choice in choices) != list(
                range(request.n or 1)
            ):
                raise InvalidToolCallError()
            return ChatCompletionResponse(
                model=request.model, choices=choices, usage=_usage(native)
            )
    finally:
        client.close()


@dataclass
class _Candidate:
    contents: list[types.Content] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    finish: FinishReason | None = None


async def stream(
    request: ChatCompletionRequest, client: genai.Client
) -> AsyncIterator[str]:
    frames = Frames(request.model)
    candidates: dict[int, _Candidate] = {}
    measured: Usage | None = None
    try:
        async with client.aio as aio:
            contents, config = parameters(request)
            native = await aio.models.generate_content_stream(
                model=request.model, contents=contents, config=config
            )
            async for chunk in native:
                measured = _usage(chunk) or measured
                for candidate in chunk.candidates or []:
                    index = candidate.index or 0
                    if index < 0 or index >= (request.n or 1):
                        raise InvalidToolCallError()
                    if index not in candidates:
                        candidates[index] = _Candidate()
                        yield frames.chunk(
                            DeltaMessage(role="assistant"), index=index
                        )
                    current = candidates[index]
                    if current.finish is not None:
                        raise InvalidToolCallError()
                    if candidate.content is not None:
                        text, calls = _projection([candidate.content])
                        current.contents.append(candidate.content)
                        if text:
                            yield frames.chunk(
                                DeltaMessage(content=text), index=index
                            )
                        for call in calls:
                            call.index = len(current.calls)
                            current.calls.append(call)
                            yield frames.chunk(
                                DeltaMessage(tool_calls=[call]), index=index
                            )
                    if candidate.finish_reason is not None:
                        current.finish = _finish(
                            candidate.finish_reason, bool(current.calls)
                        )
            if sorted(candidates) != list(range(request.n or 1)):
                raise InvalidToolCallError()
            observe_stream_usage(measured)
            for index, current in sorted(candidates.items()):
                if current.finish is None:
                    raise InvalidToolCallError()
                message = _message(current.contents, current.calls)
                yield frames.chunk(
                    DeltaMessage(provider_state=message.provider_state),
                    current.finish,
                    index,
                )
    finally:
        client.close()
    yield "data: [DONE]\n\n"
