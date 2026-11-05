"""Remote employee identity, local tool policy and terminal event contracts."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from agno.models.response import ToolExecution
from agno.run import RunStatus
from agno.run.agent import (
    RunCompletedEvent,
    RunContentEvent,
    RunOutput,
    RunOutputEvent,
    RunPausedEvent,
)
from agno.tools.function import Function
from urllib.parse import parse_qs

from app.models.internal import Agent, AgentExecutionContext
from app.runtime.supervisor.agents.builder import AgnoAgentBuilder
from app.runtime.tools.builder.agent_builder import StoreRemoteAgent
from app.runtime.tools.models import AgentConfig, AgentRunRequest


def remote(tools: list[Function] | None = None) -> StoreRemoteAgent:
    return StoreRemoteAgent(
        base_url="https://store.example.test",
        agent_id="remote-agent",
        override_id="local-agent",
        override_name="客服员工",
        api_key="fixture-key",
        tools=tools or [],
    )


def call(identifier: str = "call-1", **values: object) -> ToolExecution:
    data: dict[str, object] = {
        "tool_call_id": identifier,
        "tool_name": "query",
        "tool_args": {"value": "fixture-private-input"},
        "external_execution_required": True,
    }
    data.update(values)
    return ToolExecution(**data)


def paused(calls: list[ToolExecution], **values: object) -> RunOutput:
    data: dict[str, object] = {
        "run_id": "run-1",
        "session_id": "session-1",
        "tools": calls,
        "status": RunStatus.paused,
    }
    data.update(values)
    return RunOutput(**data)


async def output(value: RunOutput) -> RunOutput:
    return value


async def events(*values: RunOutputEvent) -> AsyncIterator[RunOutputEvent]:
    for value in values:
        yield value


@pytest.mark.asyncio
async def test_real_remote_employee_build_preserves_remote_and_local_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    employee = Agent(
        id=uuid4(),
        name="本企业员工",
        model="fixture-model",
        created_at=now,
        updated_at=now,
        is_remote_store_agent=True,
        remote_agent_url="https://store.example.test",
        store_agent_id="remote-agent",
    )
    context = AgentExecutionContext(
        agent=employee,
        project_id=str(uuid4()),
        message="测试",
        request_id="request-1",
        timeout=10,
    )
    actual = remote()
    actual._override_id = str(employee.id)
    actual._override_name = employee.name
    builder = AgnoAgentBuilder()
    monkeypatch.setattr(
        builder._agent_builder, "build_agent", AsyncMock(return_value=actual)
    )
    built = await builder.build_agent(context)
    assert built.agent is actual
    assert actual.id == str(employee.id) and actual.agent_id == "remote-agent"
    assert actual.name == employee.name
    assert actual.metadata["project_id"] == context.project_id


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["remote_agent_url", "store_agent_id"])
async def test_missing_remote_configuration_stops_before_credentials(
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.runtime.tools.builder.agent_builder import (
        AgentBuilder,
        api_service_client,
    )
    from app.runtime.core.exceptions import InvalidConfigurationError
    from app.runtime.tools.config import ToolsRuntimeSettings

    now = datetime.now(timezone.utc)
    employee = Agent(
        id=uuid4(),
        name="测试员工",
        model="fixture-model",
        created_at=now,
        updated_at=now,
        is_remote_store_agent=True,
        remote_agent_url="https://store.example.test",
        store_agent_id="remote-agent",
    ).model_copy(update={missing: None})
    credentials = AsyncMock()
    monkeypatch.setattr(
        api_service_client, "get_store_credential", credentials
    )
    request = AgentRunRequest(
        message="测试",
        config=AgentConfig(model_name="fixture-model"),
        project_id=str(uuid4()),
    )
    with pytest.raises(InvalidConfigurationError):
        await AgentBuilder(ToolsRuntimeSettings())._build_remote_store_agent(
            request, employee
        )
    credentials.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flag",
    ["requires_confirmation", "requires_user_input", "external_execution"],
)
async def test_local_policy_cannot_be_confirmed_by_remote_data(
    flag: str,
) -> None:
    execute = AsyncMock(return_value="result")
    tool = Function(name="query", entrypoint=execute, **{flag: True})
    agent = remote([tool])
    with pytest.raises(ValueError):
        await agent._execute_tools_locally(
            [call(confirmed=True, answered=True)]
        )
    execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["duplicate", "missing_id", "invalid_args", "nonfinite"]
)
async def test_invalid_batch_never_runs_its_valid_member(kind: str) -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    bad = {
        "duplicate": call(),
        "missing_id": call(tool_call_id=None),
        "invalid_args": call("call-2", tool_args="not-an-object"),
        "nonfinite": call("call-2", tool_args={"value": float("nan")}),
    }[kind]
    with pytest.raises(ValueError):
        await agent._execute_tools_locally([call(), bad])
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_function_prevents_the_whole_batch() -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    result = await agent._execute_tools_locally(
        [call(), call("call-2", tool_name="unknown")]
    )
    assert len(result) == 2 and all(item.tool_call_error for item in result)
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_native_function_hooks_run_in_order_without_printing_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    order: list[str] = []

    async def before(fc: object) -> None:
        order.append("before")

    async def around(name: str, function: object, arguments: object) -> object:
        order.append("around")
        return await function(**arguments)

    async def execute(value: str) -> str:
        order.append("execute")
        return "fixture-private-result"

    async def after(fc: object) -> None:
        order.append("after")

    agent = remote(
        [
            Function(
                name="query",
                entrypoint=execute,
                pre_hook=before,
                post_hook=after,
                tool_hooks=[around],
            )
        ]
    )
    results = await agent._execute_tools_locally([call()])
    assert results[0].result == "fixture-private-result"
    assert order == ["before", "around", "execute", "after"]
    captured = capsys.readouterr()
    assert "fixture-private" not in captured.out + captured.err


@pytest.mark.asyncio
async def test_failure_before_tool_is_closed_and_sanitized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def before(fc: object) -> None:
        raise ValueError("fixture-private-error")

    execute = AsyncMock(return_value="fixture-private-result")
    agent = remote(
        [Function(name="query", entrypoint=execute, pre_hook=before)]
    )
    results = await agent._execute_tools_locally([call()])
    execute.assert_not_called()
    assert results[0].tool_call_error
    captured = capsys.readouterr()
    assert (
        "fixture-private"
        not in captured.out + captured.err + results[0].result
    )


@pytest.mark.asyncio
async def test_entrypoint_exception_never_leaks_input_or_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def execute(value: str) -> str:
        raise ValueError("fixture-private-error")

    agent = remote([Function(name="query", entrypoint=execute)])
    results = await agent._execute_tools_locally([call()])
    assert results[0].tool_call_error
    captured = capsys.readouterr()
    assert (
        "fixture-private"
        not in captured.out + captured.err + results[0].result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["run", "session", "status"])
async def test_invalid_remote_context_is_rejected_before_actions(
    bad: str,
) -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    first = paused(
        [call()],
        **{
            "run": {"run_id": None},
            "session": {"session_id": "another-session"},
            "status": {"status": RunStatus.completed},
        }[bad],
    )
    with pytest.raises(ValueError):
        await agent._wrap_coro_with_tool_execution(
            output(first), session_id="session-1"
        )
    execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_replayed_pending_call_never_executes_twice(
    stream: bool,
) -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    if stream:

        def continuation(**kwargs: object) -> AsyncIterator[RunOutputEvent]:
            return events(RunPausedEvent(run_id="run-1", tools=[call()]))

        agent.acontinue_run = continuation
        source = agent._wrap_stream_with_tool_execution(
            events(RunPausedEvent(run_id="run-1", tools=[call()]))
        )
        with pytest.raises(ValueError):
            _ = [item async for item in source]
    else:
        agent.acontinue_run = AsyncMock(return_value=paused([call()]))
        with pytest.raises(ValueError):
            await agent._wrap_coro_with_tool_execution(
                output(paused([call()]))
            )
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_checks_terminal_before_completion() -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    seen: list[RunOutputEvent] = []
    with pytest.raises(ValueError):
        async for item in agent._wrap_stream_with_tool_execution(
            events(
                RunCompletedEvent(run_id="run-1"),
                RunPausedEvent(run_id="run-1", tools=[call()]),
            )
        ):
            seen.append(item)
    assert not any(isinstance(item, RunCompletedEvent) for item in seen)
    execute.assert_not_called()
    with pytest.raises(ValueError):
        _ = [
            item
            async for item in agent._wrap_stream_with_tool_execution(
                events(RunContentEvent(content="partial", run_id="run-1"))
            )
        ]


@pytest.mark.asyncio
async def test_stream_has_one_completion_with_local_identity() -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])

    def continuation(**kwargs: object) -> AsyncIterator[RunOutputEvent]:
        assert kwargs["updated_tools"][0].result == "result"
        return events(
            RunCompletedEvent(
                run_id="run-1",
                agent_id="remote-agent",
                agent_name="remote",
                content="完成",
            )
        )

    agent.acontinue_run = continuation
    output_events = [
        item
        async for item in agent._wrap_stream_with_tool_execution(
            events(RunPausedEvent(run_id="run-1", tools=[call()])),
            session_id="session-1",
        )
    ]
    assert len(output_events) == 1 and isinstance(
        output_events[0], RunCompletedEvent
    )
    assert (
        output_events[0].agent_id == "local-agent"
        and output_events[0].agent_name == "客服员工"
    )
    execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("grouped", [False, True])
async def test_cancellation_keeps_original_exception_and_never_logs_data(
    grouped: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FixtureCancelledError(Exception):
        pass

    cancellation = (
        ExceptionGroup(
            "fixture-private-group",
            [FixtureCancelledError("fixture-private-error")],
        )
        if grouped
        else asyncio.CancelledError()
    )

    async def execute(value: str) -> str:
        raise cancellation

    agent = remote([Function(name="query", entrypoint=execute)])
    with pytest.raises(type(cancellation)) as caught:
        await agent._execute_tools_locally([call()])
    assert caught.value is cancellation
    captured = capsys.readouterr()
    assert "fixture-private" not in captured.out + captured.err


@pytest.mark.asyncio
async def test_scope_revocation_after_action_does_not_publish_or_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.agent_execution_scope import ExecutionRevoked

    async def check() -> None:
        if execute.await_count:
            raise ExecutionRevoked()

    checks = AsyncMock(side_effect=check)
    monkeypatch.setattr(
        "app.runtime.tools.remote_tool_execution.check_current_execution",
        checks,
    )
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    continuation = AsyncMock(
        return_value=RunOutput(status=RunStatus.completed)
    )
    agent.acontinue_run = continuation
    with pytest.raises(ExecutionRevoked):
        await agent._wrap_coro_with_tool_execution(output(paused([call()])))
    execute.assert_awaited_once()
    continuation.assert_not_called()


@pytest.mark.asyncio
async def test_revocation_during_pre_hook_blocks_the_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.agent_execution_scope import ExecutionRevoked

    revoked = False

    async def before(fc: object) -> None:
        nonlocal revoked
        revoked = True

    async def check() -> None:
        if revoked:
            raise ExecutionRevoked()

    monkeypatch.setattr(
        "app.runtime.tools.remote_tool_execution.check_current_execution",
        check,
    )
    execute = AsyncMock(return_value="result")
    agent = remote(
        [Function(name="query", entrypoint=execute, pre_hook=before)]
    )
    with pytest.raises(ExecutionRevoked):
        await agent._execute_tools_locally([call()])
    execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_complete_native_arun_dispatch_and_tool_schema(
    stream: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agno.agent import RemoteAgent

    def native(
        instance: object, incoming: object, *, stream: bool, **kwargs: object
    ) -> object:
        assert incoming == "hello"
        assert '"name": "query"' in kwargs["tools"]
        if stream:
            return events(RunCompletedEvent(run_id="run-1", content="完成"))
        return output(
            RunOutput(run_id="run-1", status=RunStatus.completed, content="完成")
        )

    monkeypatch.setattr(RemoteAgent, "arun", native)
    agent = remote(
        [Function(name="query", entrypoint=AsyncMock(return_value="result"))]
    )
    if stream:
        received = [item async for item in agent.arun("hello", stream=True)]
        assert len(received) == 1 and received[0].agent_name == "客服员工"
    else:
        received = await agent.arun("hello", stream=False)
        assert received.agent_id == "local-agent" and received.content == "完成"


@pytest.mark.asyncio
async def test_mcp_authentication_link_is_returned_without_logging_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mcp import McpError
    from mcp.types import ErrorData
    from app.runtime.tools.mcp_authentication import wrap_mcp_authenticate_tool

    url = "https://auth.example.test/start?state=fixture-challenge"

    async def execute(value: str) -> str:
        raise McpError(
            ErrorData(code=-32003, message="login", data={"url": url})
        )

    agent = remote(
        [
            wrap_mcp_authenticate_tool(
                Function(name="query", entrypoint=execute)
            )
        ]
    )
    results = await agent._execute_tools_locally([call()])
    assert results[0].tool_call_error and url in results[0].result
    captured = capsys.readouterr()
    assert url not in captured.out + captured.err
    assert "fixture-private" not in captured.out + captured.err


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_actual_sdk_http_keeps_credentials_session_and_user(
    stream: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    payloads: list[dict[str, list[str]]] = []
    execute = AsyncMock(return_value="fixture-result")
    agent = remote([Function(name="query", entrypoint=execute)])

    def handler(incoming: httpx.Request) -> httpx.Response:
        requests.append(incoming)
        payloads.append(parse_qs(incoming.content.decode()))
        first = len(requests) == 1
        if not stream:
            body = (
                paused([call()])
                if first
                else RunOutput(
                    run_id="run-1",
                    session_id="session-1",
                    user_id="user-1",
                    status=RunStatus.completed,
                    content="完成",
                )
            )
            return httpx.Response(200, json=body.to_dict())
        event = (
            RunPausedEvent(
                run_id="run-1", session_id="session-1", tools=[call()]
            )
            if first
            else RunCompletedEvent(
                run_id="run-1",
                session_id="session-1",
                content="完成",
            )
        )
        return httpx.Response(
            200,
            text="event: "
            + event.event
            + "\ndata: "
            + json.dumps(event.to_dict())
            + "\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        monkeypatch.setattr(
            "agno.client.os.get_default_async_client", lambda: client
        )
        if stream:
            result = [
                item
                async for item in agent.arun(
                    "hello",
                    stream=True,
                    session_id="session-1",
                    user_id="user-1",
                    auth_token="fixture-run-token",
                )
            ]
            assert len(result) == 1 and isinstance(
                result[0], RunCompletedEvent
            )
        else:
            result = await agent.arun(
                "hello",
                stream=False,
                session_id="session-1",
                user_id="user-1",
                auth_token="fixture-run-token",
            )
            assert result.content == "完成"
    assert len(requests) == 2
    assert requests[1].url.path.endswith(
        "/agents/remote-agent/runs/run-1/continue"
    )
    for incoming, body in zip(requests, payloads):
        assert incoming.headers["x-api-key"] == "fixture-key"
        assert incoming.headers["authorization"] == "Bearer fixture-run-token"
        assert body["session_id"] == ["session-1"] and body["user_id"] == [
            "user-1"
        ]
    assert json.loads(payloads[1]["tools"][0])[0]["result"] == "fixture-result"
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_assigned_session_is_kept_for_continuation() -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    continued = AsyncMock(
        return_value=RunOutput(
            run_id="run-1", status=RunStatus.completed, content="完成"
        )
    )
    agent.acontinue_run = continued
    result = await agent._wrap_coro_with_tool_execution(
        output(paused([call()]))
    )
    assert continued.call_args.kwargs["session_id"] == "session-1"
    assert result.session_id == "session-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier",
    ["../other", "..", "run?other=1", "run%2Fother", "run\nother"],
)
async def test_remote_run_identifier_cannot_change_the_endpoint(
    identifier: str,
) -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    with pytest.raises(ValueError):
        await agent._wrap_coro_with_tool_execution(
            output(paused([call()], run_id=identifier))
        )
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_completion_with_pending_tools_is_not_success() -> None:
    execute = AsyncMock(return_value="result")
    agent = remote([Function(name="query", entrypoint=execute)])
    captured: list[RunOutputEvent] = []
    with pytest.raises(ValueError):
        async for item in agent._wrap_stream_with_tool_execution(
            events(
                RunCompletedEvent(
                    run_id="run-1", content="未实际查询", tools=[call()]
                ),
            )
        ):
            captured.append(item)
    assert not captured
    execute.assert_not_called()
