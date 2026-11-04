"""Actual SDK calls hide authority and preserve one trusted invocation ID."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid5

import httpx
import pytest
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.function import FunctionCall

from app.models.internal import AgentExecutionContext
from app.schemas.agent_device_script_capability import ScriptRuntimeDefinition
from app.schemas.agent_device_script_runs import ScriptRunView
from app.schemas.agent_device_script_selection import ScriptSelectionRecord
from app.schemas.agent_execution import AgentExecutionReference
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from app.services.agent_trial_fingerprint import configuration_fingerprint
from tests.test_agent_knowledge_trial import employee


def fixture():
    now, project, actor = datetime.now(timezone.utc), uuid4(), uuid4()
    agent = employee().model_copy(
        update={"project_id": str(project), "skills_enabled": False}
    )
    selection = ScriptSelectionRecord(
        id=uuid4(),
        project_id=project,
        agent_id=agent.id,
        request_id=uuid4(),
        configured_by=actor,
        revision=1,
        enabled=True,
        binding_id=uuid4(),
        binding_revision=1,
        report_id=uuid4(),
        review_id=uuid4(),
        saved_at=now,
        expires_at=now + timedelta(hours=1),
        selection_digest="d" * 64,
    )
    agent.device_script_selection = selection
    context = AgentExecutionContext(
        agent=agent,
        project_id=str(project),
        message="整理业务资料",
        request_id="synthetic-script-tools",
        timeout=30,
        knowledge_channel="internal",
        execution_reference=AgentExecutionReference(
            project_id=project,
            agent_id=agent.id,
            channel="internal",
            configuration_fingerprint=configuration_fingerprint(agent),
            policy_revision="a" * 64,
            business_evidence_revision="b" * 64,
        ),
    )
    definition = ScriptRuntimeDefinition(
        selection=selection,
        descriptor={
            "script_id": "report",
            "title": "读取业务汇总",
            "version": "v1",
            "grant_digest": "c" * 64,
            "effect": "read_only",
            "timeout_seconds": 10,
            "max_output_bytes": 4096,
            "max_runs": 3,
            "parameters": [
                {
                    "name": "label",
                    "kind": "string",
                    "required": True,
                    "max_length": 32,
                },
                {
                    "name": "limit",
                    "kind": "integer",
                    "required": True,
                    "minimum": 1,
                    "maximum": 3,
                },
                {"name": "urgent", "kind": "boolean", "required": False},
            ],
            "result_fields": [
                {"name": "ready", "kind": "boolean", "required": True}
            ],
        },
        remaining_runs=2,
        checked_at=now,
        valid_until=now + timedelta(seconds=30),
    )
    return context, definition


def receipt(context, definition, invocation, parameters):
    now = datetime.now(timezone.utc)
    return ScriptRunView(
        id=uuid4(),
        project_id=definition.selection.project_id,
        actor_id=definition.selection.configured_by,
        agent_id=context.agent.id,
        request_id=uuid5(
            context.execution_id, f"employee-device-script-v1:{invocation}"
        ),
        report_id=definition.selection.report_id,
        review_id=definition.selection.review_id,
        execution_id=uuid4(),
        status="succeeded",
        error_code=None,
        created_at=now,
        deadline_at=now + timedelta(seconds=30),
        dispatch_started_at=now,
        completed_at=now,
        stop_requested_at=None,
        result_digest="e" * 64,
        result_fields={"ready": True},
    )


@pytest.mark.asyncio
async def test_sdk_call_reuses_invocation_and_exposes_only_reviewed_values():
    from app.runtime.supervisor.agents.device_script_tools import (
        build_device_script_tools,
    )

    context, definition = fixture()
    runner = AsyncMock(side_effect=receipt)
    async with track_execution_scope(
        context.execution_reference,
        AsyncMock(return_value=context.execution_reference),
    ):
        tools = await build_device_script_tools(
            context,
            definition_reader=AsyncMock(return_value=definition),
            runner=runner,
        )
        assert len(tools) == 1
        tool = tools[0]
        assert set(tool.parameters["properties"]) == {
            "label",
            "limit",
            "urgent",
        }
        assert tool.parameters["additionalProperties"] is False
        for _ in range(2):
            call = FunctionCall(
                function=tool,
                call_id="call_original",
                arguments={"label": "fixture", "limit": 2},
            )
            result = await call.aexecute()
            assert result.status == "success" and json.loads(call.result) == {
                "ready": True
            }
    assert runner.await_count == 2
    assert (
        runner.await_args_list[0].args[2] == runner.await_args_list[1].args[2]
    )
    assert len(context.knowledge_evidence.tool_results) == 2
    assert context.knowledge_evidence.tool_results[0].success
    assert str(definition.selection.report_id) not in call.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,call_id",
    [
        ({"label": "fixture", "limit": 2, "request_id": "forged"}, "owned"),
        ({"label": "fixture", "limit": 2, "fc": {}}, "owned"),
        ({"label": "fixture", "limit": True}, "owned"),
        ({"label": "fixture", "limit": 4}, "owned"),
        ({"label": "fixture", "limit": 2}, None),
    ],
)
async def test_untrusted_identity_or_invalid_values_never_reach_runner(
    arguments, call_id
):
    from app.runtime.supervisor.agents.device_script_tools import (
        build_device_script_tools,
    )

    context, definition = fixture()
    runner = AsyncMock(side_effect=receipt)
    async with track_execution_scope(
        context.execution_reference,
        AsyncMock(return_value=context.execution_reference),
    ):
        (tool,) = await build_device_script_tools(
            context,
            definition_reader=AsyncMock(return_value=definition),
            runner=runner,
        )
        with pytest.raises(ExecutionRevoked):
            await FunctionCall(
                function=tool, arguments=arguments, call_id=call_id
            ).aexecute()
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_argument_change_during_precheck_is_rejected():
    from app.runtime.supervisor.agents.device_script_tools import (
        build_device_script_tools,
    )

    context, definition = fixture()
    runner = AsyncMock(side_effect=receipt)
    read = AsyncMock(return_value=context.execution_reference)
    async with track_execution_scope(context.execution_reference, read):
        (tool,) = await build_device_script_tools(
            context,
            definition_reader=AsyncMock(return_value=definition),
            runner=runner,
        )
        call = FunctionCall(
            function=tool,
            arguments={"label": "before", "limit": 2},
            call_id="owned",
        )

        async def changed(reference):
            call.arguments["label"] = "after"
            return reference

        read.side_effect = changed
        with pytest.raises(ExecutionRevoked):
            await call.aexecute()
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_entrypoint_and_wrong_result_scope_fail_closed():
    from app.runtime.supervisor.agents.device_script_tools import (
        build_device_script_tools,
    )

    context, definition = fixture()
    runner = AsyncMock(
        side_effect=lambda *args: receipt(*args).model_copy(
            update={"actor_id": uuid4()}
        )
    )
    async with track_execution_scope(
        context.execution_reference,
        AsyncMock(return_value=context.execution_reference),
    ):
        (tool,) = await build_device_script_tools(
            context,
            definition_reader=AsyncMock(return_value=definition),
            runner=runner,
        )
        with pytest.raises(ExecutionRevoked):
            await tool.entrypoint(label="fixture", limit=2)
    async with track_execution_scope(
        context.execution_reference,
        AsyncMock(return_value=context.execution_reference),
    ):
        (tool,) = await build_device_script_tools(
            context,
            definition_reader=AsyncMock(return_value=definition),
            runner=runner,
        )
        with pytest.raises(ExecutionRevoked):
            await FunctionCall(
                function=tool,
                arguments={"label": "fixture", "limit": 2},
                call_id="owned",
            ).aexecute()
    assert context.knowledge_evidence is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"disable_tools": True},
        {"response_purpose": "expression"},
        {"knowledge_channel": "web"},
        {"execution_reference": None},
    ],
)
async def test_disabled_or_unconfirmed_context_has_no_script_tools(changes):
    from app.runtime.supervisor.agents.device_script_tools import (
        build_device_script_tools,
    )

    context, _ = fixture()
    reader = AsyncMock()
    assert (
        await build_device_script_tools(
            context.model_copy(update=changes), definition_reader=reader
        )
        == []
    )
    reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_direct_builder_and_actual_agno_loop_in_both_modes(
    stream, monkeypatch
):
    from app.runtime.supervisor.agents import device_script_tools as module
    from app.runtime.supervisor.agents.builder import AgnoAgentBuilder

    context, definition = fixture()
    runner = AsyncMock(side_effect=receipt)
    monkeypatch.setattr(
        module, "_read_definition", AsyncMock(return_value=definition)
    )
    monkeypatch.setattr(module, "_run_script", runner)
    requests = []

    def model(request):
        assert request.url.host == "fixture.invalid"
        body = json.loads(request.content)
        requests.append(body)
        first = len(requests) == 1
        tool = {
            "index": 0,
            "id": "original_sdk_call",
            "type": "function",
            "function": {
                "name": "employee_device_script",
                "arguments": json.dumps({"label": "fixture", "limit": 2}),
            },
        }
        if body.get("stream"):
            delta = (
                {"role": "assistant", "tool_calls": [tool]}
                if first
                else {"content": "处理完成"}
            )
            packets = [
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture",
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": None}
                    ],
                },
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ],
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    "".join(
                        "data: " + json.dumps(packet) + "\n\n"
                        for packet in packets
                    )
                    + "data: [DONE]\n\n"
                ).encode(),
            )
        message = (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        key: value
                        for key, value in tool.items()
                        if key != "index"
                    }
                ],
            }
            if first
            else {"role": "assistant", "content": "处理完成"}
        )
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if first else "stop",
                        "message": message,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(model)
    ) as client:
        chat = OpenAIChat(
            id="fixture",
            api_key="synthetic-only",
            base_url="http://fixture.invalid/v1",
            http_client=client,
            max_retries=0,
        )
        monkeypatch.setattr(chat.get_async_client(), "_platform", "Windows")
        builder = AgnoAgentBuilder.__new__(AgnoAgentBuilder)
        builder._agent_builder = SimpleNamespace(
            build_agent=AsyncMock(
                return_value=Agent(model=chat, tools=[], telemetry=False)
            )
        )
        builder._logger = Mock()
        async with track_execution_scope(
            context.execution_reference,
            AsyncMock(return_value=context.execution_reference),
        ):
            built = await builder.build_agent(context)
            assert any(
                tool.name == "employee_device_script"
                for tool in built.agent.tools
            )
            if stream:
                response = built.agent.arun("整理业务资料", stream=True)
                events = [event async for event in response]
                assert any(
                    getattr(event, "content", None) == "处理完成"
                    for event in events
                )
            else:
                response = await built.agent.arun("整理业务资料", stream=False)
                assert response.content == "处理完成"
    assert len(requests) == 2 and runner.await_count == 1
    assert context.knowledge_evidence.tool_results[0].success
