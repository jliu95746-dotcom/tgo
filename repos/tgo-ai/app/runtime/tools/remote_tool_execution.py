"""Validate remote requests and retain native Function execution policy."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import wraps
from inspect import isawaitable
from urllib.parse import urlsplit

from agno.exceptions import AgentRunException, StopAgentRun
from agno.models.response import ToolExecution
from agno.tools import Toolkit
from agno.tools.function import Function, FunctionCall
from mcp import McpError

from app.schemas.remote_tool_execution import RemoteToolRequest
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    check_current_execution,
)

INVALID_REMOTE_TOOL = "远程工具请求或确认状态无效，后续操作已停止，请转人工核对"
TOOL_FAILED = "<error>工具执行或结果核验失败，请重试或转人工处理</error>"
TOOL_UNAVAILABLE = "<error>工具未授权或配置不可用，本批工具未执行</error>"


class RemoteToolError(ValueError):
    def __init__(self) -> None:
        super().__init__(INVALID_REMOTE_TOOL)


def is_cancellation(exc: BaseException) -> bool:
    if (
        isinstance(exc, asyncio.CancelledError)
        or "cancel" in type(exc).__name__.lower()
    ):
        return True
    return isinstance(exc, BaseExceptionGroup) and any(
        is_cancellation(nested) for nested in exc.exceptions
    )


def normalize_tools(tools: Sequence[object]) -> dict[str, Function]:
    result: dict[str, Function] = {}

    def add(value: object) -> None:
        if isinstance(value, Function):
            function = value
        elif callable(value):
            function = Function.from_callable(value)
        else:
            raise RemoteToolError()
        if not function.name.strip() or function.name in result:
            raise RemoteToolError()
        result[function.name] = function

    for tool in tools:
        if isinstance(tool, Toolkit):
            for function in tool.get_async_functions().values():
                add(function)
        else:
            add(tool)
    return result


def pending_tools(
    calls: Sequence[ToolExecution | Mapping[str, object]],
) -> list[ToolExecution]:
    result: list[ToolExecution] = []
    identifiers: set[str] = set()
    try:
        for call in calls:
            if not isinstance(call, (ToolExecution, Mapping)):
                raise RemoteToolError()
            data: Mapping[str, object] = (
                {
                    "tool_call_id": call.tool_call_id,
                    "tool_name": call.tool_name,
                    "tool_args": call.tool_args,
                    "requires_confirmation": call.requires_confirmation,
                    "requires_user_input": call.requires_user_input,
                    "external_execution_required": (
                        call.external_execution_required
                    ),
                }
                if isinstance(call, ToolExecution)
                else call
            )
            external = data.get("external_execution_required")
            if external is not None and type(external) is not bool:
                raise RemoteToolError()
            if external is not True:
                continue
            request = RemoteToolRequest.model_validate(
                {
                    **data,
                    "tool_args": data.get("tool_args")
                    if data.get("tool_args") is not None
                    else {},
                }
            )
            if (
                not request.tool_call_id.strip()
                or not request.tool_name.strip()
                or request.tool_call_id in identifiers
                or request.requires_confirmation
                or request.requires_user_input
            ):
                raise RemoteToolError()
            # Strict JSON snapshot; never coerce non-finite numbers or objects.
            encoded = json.dumps(request.tool_args, allow_nan=False)
            if len(encoded) > 524288:
                raise RemoteToolError()
            identifiers.add(request.tool_call_id)
            normalized = (
                replace(call)
                if isinstance(call, ToolExecution)
                else ToolExecution()
            )
            normalized.tool_call_id = request.tool_call_id
            normalized.tool_name = request.tool_name
            normalized.tool_args = request.tool_args
            normalized.external_execution_required = True
            result.append(normalized)
    except (TypeError, ValueError, RecursionError):
        raise RemoteToolError() from None
    return result


class _SafeToolFailure(AgentRunException):
    def __init__(self) -> None:
        super().__init__("工具执行或核验失败")


@dataclass
class _FailureState:
    cancellation: BaseException | None = None
    stop: StopAgentRun | None = None
    public_error: str | None = None


def _authentication_error(exc: BaseException) -> str | None:
    pending = [exc]
    seen: set[int] = set()
    while pending and len(seen) < 32:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, McpError) and item.error.code == -32003:
            data: object = item.error.data
            url: object = data.get("url") if isinstance(data, dict) else None
            if (
                isinstance(url, str)
                and len(url) <= 2048
                and not any(ord(char) < 32 for char in url)
            ):
                try:
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme in {"http", "https"}
                        and parsed.hostname
                        and not parsed.username
                        and not parsed.password
                    ):
                        return "<error>工具需要认证，请先登录并核对执行结果：" + url + "</error>"
                except ValueError:
                    pass
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions)
        if item.__cause__ is not None:
            pending.append(item.__cause__)
    return None


def _guard(
    callback: Callable[..., object],
    state: _FailureState,
    check_execution: bool = False,
) -> Callable[..., object]:
    @wraps(callback)
    async def guarded(*args: object, **kwargs: object) -> object:
        try:
            if check_execution:
                await check_current_execution()
            value = callback(*args, **kwargs)
            value = await value if isawaitable(value) else value
            if check_execution:
                await check_current_execution()
            return value
        except BaseException as exc:
            if is_cancellation(exc):
                state.cancellation = state.cancellation or exc
            elif isinstance(exc, ExecutionRevoked):
                state.stop = exc
            elif isinstance(exc, StopAgentRun):
                state.stop = StopAgentRun("工具已要求停止，后续操作已终止")
            elif not isinstance(exc, Exception):
                raise
            else:
                state.public_error = _authentication_error(exc)
            # The SDK catches AgentRunException without printing original data.
            raise _SafeToolFailure() from None

    return guarded


class _PrivateFunctionCall(FunctionCall):
    def get_call_str(self) -> str:
        return "remote_tool(parameters redacted)"


async def _execute(call: ToolExecution, function: Function) -> ToolExecution:
    state = _FailureState()
    copied = function.model_copy()
    if copied.entrypoint is None:
        raise RemoteToolError()
    copied.entrypoint = _guard(copied.entrypoint, state, check_execution=True)
    copied.cache_results = False
    if copied.pre_hook is not None:
        copied.pre_hook = _guard(copied.pre_hook, state)
    if copied.post_hook is not None:
        copied.post_hook = _guard(copied.post_hook, state)
    if copied.tool_hooks is not None:
        copied.tool_hooks = [_guard(hook, state) for hook in copied.tool_hooks]
    native = _PrivateFunctionCall(
        function=copied, arguments=call.tool_args, call_id=call.tool_call_id
    )
    try:
        executed = await native.aexecute()
        if state.cancellation is not None:
            raise state.cancellation
        if state.stop is not None:
            raise state.stop
        value: object = executed.result
        if executed.status != "success":
            return replace(
                call,
                result=TOOL_FAILED,
                tool_call_error=True,
                external_execution_required=False,
            )
        rendered = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return replace(
            call,
            result=rendered,
            tool_call_error=rendered.startswith("<error>"),
            stop_after_tool_call=bool(
                function.stop_after_tool_call or call.stop_after_tool_call
            ),
            external_execution_required=False,
        )
    except BaseException as exc:
        if state.cancellation is not None:
            raise state.cancellation
        if state.stop is not None:
            raise state.stop
        if is_cancellation(exc) or not isinstance(exc, Exception):
            raise
        return replace(
            call,
            result=state.public_error or TOOL_FAILED,
            tool_call_error=True,
            external_execution_required=False,
        )


async def execute_remote_tools(
    registry: Mapping[str, object],
    calls: Sequence[ToolExecution | Mapping[str, object]],
) -> list[ToolExecution]:
    prepared = pending_tools(calls)
    functions: dict[str, Function] = {}
    unavailable = False
    for call in prepared:
        name = call.tool_name or ""
        tool = registry.get(name)
        if isinstance(tool, Function):
            function = tool.model_copy()
        elif callable(tool):
            function = Function.from_callable(tool)
        else:
            unavailable = True
            continue
        if (
            function.requires_confirmation
            or function.requires_user_input
            or function.external_execution
        ):
            raise RemoteToolError()
        if function.entrypoint is None:
            unavailable = True
        functions[name] = function
    if unavailable:
        await check_current_execution()
        return [
            replace(
                call,
                result=TOOL_UNAVAILABLE,
                tool_call_error=True,
                external_execution_required=False,
            )
            for call in prepared
        ]
    result: list[ToolExecution] = []
    for call in prepared:
        await check_current_execution()
        result.append(await _execute(call, functions[call.tool_name or ""]))
        await check_current_execution()
    return result
