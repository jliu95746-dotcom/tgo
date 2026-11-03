"""Real Agno registration binds tools to server-owned context and safe receipts."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.exceptions import AuthorizationError
from app.models.internal import AgentExecutionContext
from app.schemas.agent_business_connections import AgentBusinessConnectionRecord
from app.schemas.agent_business_execution import (
    BusinessExecutionAuthority,
    BusinessExecutionReceipt,
)
from app.schemas.agent_execution import AgentExecutionReference
from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
from tests.test_business_execution_context import fixture
from tests.test_agent_knowledge_trial import employee


def runtime():
    check, principal = fixture()
    agent = employee().model_copy(
        update={"project_id": str(check.project_id), "skills_enabled": False}
    )
    binding = AgentBusinessConnectionRecord(
        id=uuid4(),
        project_id=check.project_id,
        agent_id=agent.id,
        connection_id=principal.bindings[0].connection_id,
        operations=["order_query", "logistics_query"],
        enabled=True,
        revision=2,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        configured_by=uuid4(),
    )
    agent.business_connections = [binding]
    reference = AgentExecutionReference(
        project_id=check.project_id,
        agent_id=agent.id,
        channel="web",
        configuration_fingerprint="a" * 64,
        policy_revision="b" * 64,
        business_evidence_revision="c" * 64,
    )
    context = AgentExecutionContext(
        agent=agent,
        project_id=str(check.project_id),
        message="查询订单",
        request_id=str(check.request_id),
        business_principal=principal,
        business_context_token=check.context_token,
        execution_reference=reference,
        knowledge_channel="web",
        timeout=30,
    )
    return context


def receipt(command, *, passed=True):
    authority = BusinessExecutionAuthority(
        **command.identity(),
        actor_id=uuid4(),
        phase="after_query",
        connection_revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        report_id=uuid4(),
        review_id=uuid4(),
        configuration_test_id=uuid4(),
        checked_at=datetime.now(UTC),
        expires_at=runtime().business_principal.expires_at,
    )
    return BusinessExecutionReceipt(
        **command.identity(),
        authority=authority,
        outcome="passed" if passed else "failed",
        summary={"status": "已发货"} if passed else None,
        error_code=None if passed else "denied",
        checked_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_real_agno_function_accepts_only_order_and_records_verified_evidence():
    from app.runtime.supervisor.agents.business_tools import build_business_tools

    context = runtime()
    query = AsyncMock(side_effect=lambda command: receipt(command))
    tools = build_business_tools(context, query=query)
    assert len(tools) == 2
    for tool in tools:
        assert set(tool.parameters["properties"]) == {"order_no"}
        assert len(tool.name) <= 64
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ):
        answer = await tools[0].entrypoint(order_no="ORDER-001")
    command = query.await_args.args[0]
    assert command.reference == context.execution_reference
    assert (
        command.binding_revision == 2
        and command.context.request_id == context.business_principal.request_id
    )
    assert command.context.context_token == context.business_context_token
    assert (
        "已发货" in answer
        and "private-context" not in answer
        and "ORDER-001" not in answer
    )
    assert len(context.knowledge_evidence.tool_results) == 1
    assert context.knowledge_evidence.tool_results[0].success
    assert context.knowledge_evidence.tool_results[0].query_id == command.query_id
    assert context.knowledge_evidence.tool_results[0].operation == command.operation
    assert str(command.query_id) not in answer
    from app.services.current_knowledge import evidence_prompt
    assert str(command.query_id) not in evidence_prompt(context.knowledge_evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
async def test_customer_tool_uses_current_session_without_order_argument(operation):
    from app.runtime.supervisor.agents.business_tools import build_business_tools

    context = runtime()
    context.agent.business_connections[0].operations = [operation]

    def customer_receipt(command):
        assert command.operation == operation and command.order_no is None
        original = receipt(command, passed=False)
        return BusinessExecutionReceipt.model_validate({
            **original.model_dump(mode="json"),
            "outcome": "passed", "error_code": None,
            "summary": {"status": "查询完成", "segment": "VIP", "service_tags": ["常客"]}
            if operation == "customer_profile_query"
            else {"status": "查询完成", "tier": "金卡", "points_balance": 120},
        })

    query = AsyncMock(side_effect=customer_receipt)
    tools = build_business_tools(context, query=query)
    assert len(tools) == 1
    assert tools[0].parameters["properties"] == {}
    assert tools[0].parameters["required"] == []
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ):
        answer = await tools[0].entrypoint()
    assert "查询完成" in answer
    assert "private-context" not in answer
    assert context.knowledge_evidence.tool_results[0].success


@pytest.mark.asyncio
async def test_denied_result_is_not_promoted_to_factual_success():
    from app.runtime.supervisor.agents.business_tools import build_business_tools
    from app.runtime.supervisor.agents.runner import AgnoAgentRunner
    from types import SimpleNamespace

    context = runtime()
    query = AsyncMock(side_effect=lambda command: receipt(command, passed=False))
    tool = build_business_tools(context, query=query)[0]
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ):
        answer = await tool.entrypoint(order_no="ORDER-001")
    AgnoAgentRunner._capture_tool_evidence(
        context,
        SimpleNamespace(tool_name=tool.name, result=answer, tool_call_error=False),
    )
    assert context.knowledge_evidence.factual_text() == ""
    assert len(context.knowledge_evidence.tool_results) == 1
    assert not context.knowledge_evidence.tool_results[0].success
    assert context.knowledge_evidence.tool_results[0].query_id == query.await_args.args[0].query_id


@pytest.mark.asyncio
async def test_revoked_authority_stops_current_model_owner():
    from app.runtime.supervisor.agents.business_tools import build_business_tools

    context = runtime()
    tool = build_business_tools(
        context, query=AsyncMock(side_effect=AuthorizationError("private-marker"))
    )[0]
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ) as scope:
        with pytest.raises(ExecutionRevoked) as error:
            await tool.entrypoint(order_no="ORDER-001")
        assert scope.revoked
        assert "private-marker" not in str(error.value)


@pytest.mark.parametrize(
    "fault", ["context", "reference", "disabled", "expression", "foreign_binding"]
)
def test_missing_or_other_purpose_context_never_registers_tools(fault):
    from app.runtime.supervisor.agents.business_tools import build_business_tools

    context = runtime()
    if fault == "context":
        context.business_principal = None
    elif fault == "reference":
        context.execution_reference = None
    elif fault == "disabled":
        context.disable_tools = True
    elif fault == "expression":
        context.response_purpose = "expression"
    else:
        context.agent.business_connections[0].agent_id = uuid4()
    assert build_business_tools(context) == []


@pytest.mark.asyncio
async def test_actual_supervisor_builder_installs_real_agno_functions_and_disables_debug():
    from agno.agent import Agent
    from app.runtime.supervisor.agents.builder import AgnoAgentBuilder

    context = runtime()
    builder = AgnoAgentBuilder()
    agent = Agent(debug_mode=True)
    builder._agent_builder.build_agent = AsyncMock(return_value=agent)
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ) as scope:
        built = await builder.build_agent(context)
        runtime_request = builder._agent_builder.build_agent.await_args.args[0]
        assert runtime_request.request_id == context.request_id
        assert runtime_request.business_principal == context.business_principal
        assert "business_principal" not in runtime_request.model_dump()
        assert len(built.agent.tools) == 2 and built.agent.debug_mode is False
        assert built.agent.tool_hooks[0] == scope.tool_hook
        assert all(
            set(tool.parameters["properties"]) == {"order_no"}
            for tool in built.agent.tools
        )


@pytest.mark.asyncio
async def test_streaming_tool_events_do_not_repeat_private_order_or_promote_failed_output():
    from agno.agent import (
        RunCompletedEvent,
        ToolCallStartedEvent,
        ToolCallCompletedEvent,
    )
    from agno.models.response import ToolExecution
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.runtime.supervisor.agents.business_tools import build_business_tools
    from app.runtime.supervisor.agents.runner import AgnoAgentRunner

    context = runtime()
    query = AsyncMock(side_effect=lambda command: receipt(command, passed=False))
    function = build_business_tools(
        context,
        query=query,
    )[0]

    async def events():
        answer = await function.entrypoint(order_no="PRIVATE-ORDER-001")
        tool = ToolExecution(
            tool_name=function.name,
            tool_args={"order_no": "PRIVATE-ORDER-001"},
            result=f"{answer} PRIVATE-ORDER-001",
        )
        yield ToolCallStartedEvent(tool=tool)
        yield ToolCallCompletedEvent(tool=tool)
        yield RunCompletedEvent(content="当前无法确认订单，请转人工核实。")

    emitter = Mock()
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ):
        result = await AgnoAgentRunner().stream(
            SimpleNamespace(agent=SimpleNamespace(arun=Mock(return_value=events()))),
            context,
            emitter,
            execution_id="owned-run",
        )
    assert result.success
    assert emitter.emit_agent_tool_call_started.call_args.kwargs["tool_input"] is None
    assert emitter.emit_agent_tool_call_completed.call_args.kwargs["tool_input"] is None
    assert emitter.emit_agent_tool_call_completed.call_args.kwargs["tool_output"] is None
    links = emitter.emit_agent_tool_call_completed.call_args.kwargs["business_query_links"]
    assert [(link.query_id, link.operation, link.outcome) for link in links] == [
        (query.await_args.args[0].query_id, "order_query", "failed"),
    ]
    assert "PRIVATE-ORDER-001" not in str(links)
    assert (
        context.knowledge_evidence.factual_text() == ""
        and len(context.knowledge_evidence.tool_results) == 1
    )


@pytest.mark.asyncio
async def test_parallel_business_receipts_are_emitted_once_without_call_order_guessing():
    from agno.agent import RunCompletedEvent, ToolCallCompletedEvent
    from agno.models.response import ToolExecution
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.runtime.supervisor.agents.business_tools import build_business_tools
    from app.runtime.supervisor.agents.runner import AgnoAgentRunner

    context = runtime()
    query = AsyncMock(side_effect=lambda command: receipt(command))
    function = build_business_tools(context, query=query)[0]

    async def events():
        first = await function.entrypoint(order_no="ORDER-001")
        second = await function.entrypoint(order_no="ORDER-002")
        yield ToolCallCompletedEvent(tool=ToolExecution(tool_name=function.name, result=second))
        yield ToolCallCompletedEvent(tool=ToolExecution(tool_name=function.name, result=first))
        yield RunCompletedEvent(content="查询完毕")

    emitter = Mock()
    async with track_execution_scope(
        context.execution_reference, AsyncMock(return_value=context.execution_reference)
    ):
        result = await AgnoAgentRunner().stream(
            SimpleNamespace(agent=SimpleNamespace(arun=Mock(return_value=events()))),
            context, emitter, execution_id="owned-run",
        )
    assert result.success
    event_calls = emitter.emit_agent_tool_call_completed.call_args_list
    assert len(event_calls) == 2
    assert {link.query_id for link in event_calls[0].kwargs["business_query_links"]} == {
        call.args[0].query_id for call in query.await_args_list
    }
    assert event_calls[1].kwargs["business_query_links"] == []
