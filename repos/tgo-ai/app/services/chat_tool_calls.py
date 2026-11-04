"""Validate complete tool batches and assemble provider streaming deltas."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from app.schemas.chat import FunctionCall, ToolCall

INVALID_TOOL_CALL_MESSAGE = (
    "模型输出未完成或工具调用格式错误，本轮工具未执行，请重试或转人工处理"
)


class InvalidToolCallError(ValueError):
    def __init__(self) -> None:
        super().__init__(INVALID_TOOL_CALL_MESSAGE)


def _reject_constant(value: str) -> NoReturn:
    raise InvalidToolCallError()


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise InvalidToolCallError()
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise InvalidToolCallError()
        result[name] = value
    return result


def _json_object(text: str) -> dict[str, object]:
    try:
        value: object = json.loads(
            text, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant, parse_float=_finite_float,
        )
        return _object(value)
    except (ValueError, TypeError, RecursionError):
        raise InvalidToolCallError() from None


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InvalidToolCallError()
    result: dict[str, object] = {}
    for name, item in value.items():
        if not isinstance(name, str):
            raise InvalidToolCallError()
        result[name] = item
    return result


@dataclass(frozen=True)
class PreparedToolCall:
    identifier: str
    name: str
    arguments: str

    def protocol_call(self) -> ToolCall:
        return ToolCall(
            id=self.identifier, type="function",
            function=FunctionCall(name=self.name, arguments=self.arguments),
        )


def validate_tool_calls(
    calls: Sequence[ToolCall], finish_reason: str | None,
) -> tuple[PreparedToolCall, ...]:
    """Check every call before any coroutine or history entry is created."""
    if calls and finish_reason != "tool_calls":
        raise InvalidToolCallError()
    prepared: list[PreparedToolCall] = []
    identifiers: set[str] = set()
    for call in calls:
        identifier, function = call.id, call.function
        if (
            identifier is None or not identifier.strip()
            or identifier in identifiers or function is None
            or function.name is None or not function.name.strip()
            or function.arguments is None
        ):
            raise InvalidToolCallError()
        _json_object(function.arguments)
        identifiers.add(identifier)
        prepared.append(PreparedToolCall(
            identifier, function.name, function.arguments,
        ))
    return tuple(prepared)


@dataclass
class _PartialToolCall:
    identifier: str = ""
    name: str = ""
    arguments: str = ""


class ToolCallStream:
    """Observe choice zero without executing partial or failed model output."""

    def __init__(self) -> None:
        self.content = ""
        self._calls: dict[int, _PartialToolCall] = {}
        self._finish_reason: str | None = None
        self._done = False

    def observe(self, raw: str) -> bool:
        """Return whether this original frame should reach the caller."""
        text = raw.strip()
        if self._done:
            raise InvalidToolCallError()
        if text == "data: [DONE]":
            self._done = True
            return False
        if not text or text.startswith(":"):
            return True
        if not text.startswith("data:"):
            raise InvalidToolCallError()
        payload = _json_object(text[5:].strip())
        if "error" in payload or payload.get("object") != (
            "chat.completion.chunk"
        ):
            raise InvalidToolCallError()
        choices = payload.get("choices")
        if not isinstance(choices, list):
            raise InvalidToolCallError()
        seen_choice_zero = False
        for value in choices:
            choice = _object(value)
            index = choice.get("index", 0)
            if type(index) is not int or index < 0:
                raise InvalidToolCallError()
            if index != 0:
                continue
            if seen_choice_zero:
                raise InvalidToolCallError()
            seen_choice_zero = True
            self._observe_choice(choice)
        return True

    def _observe_choice(self, choice: dict[str, object]) -> None:
        raw_delta = choice.get("delta")
        delta = {} if raw_delta is None else _object(raw_delta)
        if self._finish_reason is not None and (
            delta.get("content") or delta.get("tool_calls")
        ):
            raise InvalidToolCallError()
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise InvalidToolCallError()
            self.content += content
        calls = delta.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list):
                raise InvalidToolCallError()
            for call in calls:
                self._append_call(_object(call))
        reason = choice.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str) or reason not in {
                "stop", "length", "tool_calls", "content_filter",
            }:
                raise InvalidToolCallError()
            if self._finish_reason is not None and (
                self._finish_reason != reason
            ):
                raise InvalidToolCallError()
            self._finish_reason = reason

    def _append_call(self, delta: dict[str, object]) -> None:
        index = delta.get("index", 0)
        if type(index) is not int or index < 0:
            raise InvalidToolCallError()
        kind = delta.get("type")
        if kind is not None and kind != "function":
            raise InvalidToolCallError()
        partial = self._calls.setdefault(index, _PartialToolCall())
        identifier = delta.get("id")
        if identifier is not None:
            if not isinstance(identifier, str):
                raise InvalidToolCallError()
            if identifier:
                if partial.identifier and partial.identifier != identifier:
                    raise InvalidToolCallError()
                partial.identifier = identifier
        function = delta.get("function")
        if function is None:
            return
        for name, value in _object(function).items():
            if name not in {"name", "arguments"} or value is None:
                continue
            if not isinstance(value, str):
                raise InvalidToolCallError()
            if name == "name":
                partial.name += value
            else:
                partial.arguments += value

    def finish(self) -> tuple[PreparedToolCall, ...]:
        if not self._done:
            raise InvalidToolCallError()
        if not self._calls:
            if self._finish_reason == "tool_calls":
                raise InvalidToolCallError()
            return ()
        calls = [ToolCall(
            id=self._calls[index].identifier,
            function=FunctionCall(
                name=self._calls[index].name,
                arguments=self._calls[index].arguments,
            ),
        ) for index in sorted(self._calls)]
        return validate_tool_calls(calls, self._finish_reason)
