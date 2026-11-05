"""Text-proxy history and frames; native SDK tools never execute here."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from app.schemas.chat import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatMessage,
    DeltaMessage,
    StreamChoice,
    ToolCall,
    create_completion_id,
)
from app.services.chat_tool_calls import (
    InvalidToolCallError,
    PreparedToolCall,
    _json_object,
    validate_tool_calls,
)

FinishReason: TypeAlias = Literal[
    "stop", "length", "tool_calls", "content_filter"
]


@dataclass(frozen=True)
class ToolExchange:
    assistant: ChatMessage
    calls: tuple[PreparedToolCall, ...]
    results: tuple[ChatMessage, ...]


def history(
    messages: Sequence[ChatMessage],
) -> tuple[str, list[ChatMessage | ToolExchange]]:
    system: list[str] = []
    entries: list[ChatMessage | ToolExchange] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        index += 1
        if message.role != "assistant" and (
            message.provider_state is not None or message.tool_calls
        ):
            raise InvalidToolCallError()
        if message.role == "system":
            system.append(message.content or "")
        elif message.role == "tool":
            raise InvalidToolCallError()
        elif message.role == "assistant" and message.tool_calls:
            calls = validate_tool_calls(message.tool_calls, "tool_calls")
            results: dict[str, ChatMessage] = {}
            expected = {call.identifier for call in calls}
            while index < len(messages) and messages[index].role == "tool":
                result = messages[index]
                if (
                    result.tool_call_id is None
                    or result.tool_call_id not in expected
                    or result.tool_call_id in results
                    or result.provider_state is not None
                    or result.tool_calls
                ):
                    raise InvalidToolCallError()
                results[result.tool_call_id] = result
                index += 1
            if set(results) != expected:
                raise InvalidToolCallError()
            entries.append(
                ToolExchange(
                    message,
                    calls,
                    tuple(results[call.identifier] for call in calls),
                )
            )
        else:
            entries.append(message)
    return "\n\n".join(system), entries


@dataclass(frozen=True)
class ToolPolicy:
    mode: Literal["auto", "none", "required", "named"]
    name: str | None = None


def tool_policy(request: ChatCompletionRequest) -> ToolPolicy:
    choice: object = request.tool_choice
    if choice is None or choice == "auto":
        return ToolPolicy("auto")
    if choice in ("none", "required"):
        return ToolPolicy("none" if choice == "none" else "required")
    if isinstance(choice, dict) and choice.get("type") == "function":
        function = choice.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if (
            isinstance(name, str)
            and name
            and any(tool.function.name == name for tool in request.tools or [])
        ):
            return ToolPolicy("named", name)
    raise InvalidToolCallError()


def enforce_tool_policy(
    request: ChatCompletionRequest,
    calls: Sequence[PreparedToolCall],
) -> None:
    policy = tool_policy(request)
    if calls and (
        policy.mode == "none"
        or (
            policy.mode == "named"
            and any(call.name != policy.name for call in calls)
        )
    ):
        raise InvalidToolCallError()


def verify_projection(
    message: ChatMessage,
    content: str,
    calls: Sequence[ToolCall],
) -> None:
    expected = validate_tool_calls(message.tool_calls or [], "tool_calls")
    actual = validate_tool_calls(calls, "tool_calls")
    if (message.content or "") != content or len(expected) != len(actual):
        raise InvalidToolCallError()
    for left, right in zip(expected, actual):
        if (left.identifier, left.name) != (right.identifier, right.name) or (
            _json_object(left.arguments) != _json_object(right.arguments)
        ):
            raise InvalidToolCallError()


def arguments(value: str) -> dict[str, object]:
    return _json_object(value)


def encode_arguments(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    _json_object(encoded)
    return encoded


class Frames:
    def __init__(self, model: str) -> None:
        self.model = model
        self.identifier = create_completion_id()
        self.created = int(time.time())

    def chunk(
        self,
        delta: DeltaMessage,
        finish: FinishReason | None = None,
        index: int = 0,
    ) -> str:
        value = ChatCompletionChunk(
            id=self.identifier,
            created=self.created,
            model=self.model,
            choices=[
                StreamChoice(index=index, delta=delta, finish_reason=finish)
            ],
        )
        return f"data: {value.model_dump_json()}\n\n"
