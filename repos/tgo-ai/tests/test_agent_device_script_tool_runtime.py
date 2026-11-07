"""Fresh sessions, original SDK calls and the actual durable run gate agree."""

import json
from unittest.mock import AsyncMock

import pytest
from agno.tools.function import FunctionCall

from app.exceptions import ConflictError, ExternalServiceError
from app.models.agent import Agent
from app.runtime.supervisor.agents.device_script_tools import (
    build_device_script_tools,
)
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_device_script_capabilities import (
    AgentDeviceScriptCapabilityService,
)
from app.services.agent_device_script_reviews import AgentDeviceScriptReviews
from app.services.agent_device_script_selection import ScriptSelectionService
from app.services.agent_device_script_tool_runtime import (
    AgentDeviceScriptToolRuntime,
)
from app.services.agent_execution_reference import reference_for
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from tests.test_agent_device_script_cases import PRIVATE, service
from tests.test_agent_device_script_runtime import context_for
from tests.test_agent_device_script_selection import select_command
from tests.test_agent_device_script_selection import (
    approved_run as approved_run,
    binding_database as binding_database,
    selected_db as selected_db,
    trial_db as trial_db,
)


@pytest.mark.asyncio
async def test_live_definition_and_sdk_recovery_preserve_one_device_dispatch(
    selected_db, monkeypatch
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        cases = service(db, owner)
        selected = await ScriptSelectionService(cases).save(
            project, actor, agent, select_command(start)
        )
        capabilities = AgentDeviceScriptCapabilityService(cases)
        stored = await db.get(Agent, agent)
        original_name = stored.name
        stored.name = "uncommitted-capability-observation"
        await db.flush()
        observed = await capabilities.read(selected)
        assert observed.remaining_runs == 2 and observed.can_enable is False
        await db.rollback()
        async with sessions() as other:
            assert (await other.get(Agent, agent)).name == original_name

        original_configuration = owner.validate_script_configuration

        async def changed_definition(project, device):
            bound, item = await original_configuration(project, device)
            item.title += "已变更"
            return bound, item

        with monkeypatch.context() as patch:
            patch.setattr(
                owner, "validate_script_configuration", changed_definition
            )
            with pytest.raises(ConflictError):
                await capabilities.read(selected)
        original_budget = owner.read_script_budget

        async def exhausted(*args):
            return (await original_budget(*args)).model_copy(
                update={
                    "remaining_runs": 0,
                    "server_policy_used": 3,
                    "server_grant_used": 3,
                    "device_policy_used": 3,
                    "device_grant_used": 3,
                }
            )

        with monkeypatch.context() as patch:
            patch.setattr(owner, "read_script_budget", exhausted)
            with pytest.raises(ConflictError, match="次数"):
                await capabilities.read(selected)
            assert (
                await capabilities.read(selected, require_capacity=False)
            ).remaining_runs == 0

        context, policy = await context_for(db, project, actor, agent)
        policy.template_id = "assistant"
        await db.commit()
        context.agent.skills_enabled = False
        context.execution_reference = await reference_for(
            db, project, context.agent, KnowledgeChannel.INTERNAL
        )

    runtime = AgentDeviceScriptToolRuntime(sessions, owner, owner)
    owner.failure = ExternalServiceError(
        "device-control", "synthetic lost response"
    )
    original_read = owner.read_script
    with monkeypatch.context() as patch:
        patch.setattr(
            owner,
            "read_script",
            AsyncMock(
                side_effect=ExternalServiceError(
                    "device-control", "synthetic unknown"
                )
            ),
        )
        async with track_execution_scope(
            context.execution_reference,
            AsyncMock(return_value=context.execution_reference),
        ):
            (tool,) = await build_device_script_tools(
                context,
                definition_reader=runtime.definition,
                runner=runtime.execute,
            )
            first = FunctionCall(
                function=tool,
                arguments={"label": PRIVATE},
                call_id="original-sdk-call",
            )
            await first.aexecute()
            assert "尚未确认" in first.result and owner.posts == 2
            repeat = FunctionCall(
                function=tool,
                arguments={"label": PRIVATE},
                call_id="original-sdk-call",
            )
            await repeat.aexecute()
            assert "尚未确认" in repeat.result and owner.posts == 2
            with pytest.raises(ExecutionRevoked):
                await FunctionCall(
                    function=tool,
                    arguments={"label": PRIVATE},
                    call_id="forbidden-new-call",
                ).aexecute()
            assert owner.posts == 2
    assert owner.read_script == original_read
    owner.failure = None
    async with track_execution_scope(
        context.execution_reference,
        AsyncMock(return_value=context.execution_reference),
    ):
        (tool,) = await build_device_script_tools(
            context,
            definition_reader=runtime.definition,
            runner=runtime.execute,
        )
        for call_id in ("original-sdk-call", "last-sdk-call", "last-sdk-call"):
            call = FunctionCall(
                function=tool, arguments={"label": PRIVATE}, call_id=call_id
            )
            await call.aexecute()
            assert json.loads(call.result) == {"ready": True, "label": PRIVATE}
        assert owner.posts == 3
    assert context.knowledge_evidence.tool_results[-1].success
    assert not context.knowledge_evidence.tool_results[0].success
    assert (
        context.knowledge_evidence.tool_results[0].query_id
        == context.knowledge_evidence.tool_results[2].query_id
    )
    with pytest.raises(ConflictError, match="次数"):
        await runtime.definition(context)

    async def revoke_during_read(*args):
        budget = await original_budget(*args)
        async with sessions() as writer:
            await AgentDeviceScriptReviews(service(writer, owner)).revoke(
                project, actor, agent, start.report_id, start.review_id
            )
        return budget

    with monkeypatch.context() as patch:
        patch.setattr(owner, "read_script_budget", revoke_during_read)
        async with sessions() as db:
            with pytest.raises(ConflictError, match="变化"):
                await AgentDeviceScriptCapabilityService(
                    service(db, owner)
                ).read(selected, require_capacity=False)
    assert owner.posts == 3
