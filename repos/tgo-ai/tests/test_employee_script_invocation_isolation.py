"""Isolate SDK task identity and serialize device dispatch."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from agno.tools.function import FunctionCall

from app.runtime.supervisor.agents.device_script_tools import (
    build_device_script_tools,
)
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from tests.test_employee_device_script_tools import fixture, receipt


@pytest.mark.asyncio
async def test_parallel_sdk_calls_have_distinct_serial_invocations():
    context, definition = fixture()
    active, peak = 0, 0
    identifiers = []

    async def runner(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        identifiers.append(args[2])
        await asyncio.sleep(0)
        active -= 1
        return receipt(*args)

    async with track_execution_scope(
        context.execution_reference,
        AsyncMock(return_value=context.execution_reference),
    ):
        (tool,) = await build_device_script_tools(
            context,
            definition_reader=AsyncMock(return_value=definition),
            runner=runner,
        )
        calls = [
            FunctionCall(
                function=tool,
                arguments={"label": "fixture", "limit": 2},
                call_id=f"call-{index}",
            )
            for index in range(2)
        ]
        results = await asyncio.gather(*(call.aexecute() for call in calls))
        assert all(result.status == "success" for result in results)
    assert peak == 1 and len(set(identifiers)) == 2
    assert len(context.knowledge_evidence.tool_results) == 2


@pytest.mark.asyncio
async def test_child_task_cannot_reuse_the_parent_sdk_invocation():
    context, definition = fixture()
    runner = AsyncMock(side_effect=receipt)
    with pytest.raises(ExecutionRevoked):
        async with track_execution_scope(
            context.execution_reference,
            AsyncMock(return_value=context.execution_reference),
        ):
            (tool,) = await build_device_script_tools(
                context,
                definition_reader=AsyncMock(return_value=definition),
                runner=runner,
            )
            call = FunctionCall(
                function=tool,
                arguments={"label": "fixture", "limit": 2},
                call_id="original",
            )
            await tool.pre_hook(fc=call)
            await asyncio.create_task(
                tool.entrypoint(label="fixture", limit=2)
            )
    runner.assert_not_awaited()
