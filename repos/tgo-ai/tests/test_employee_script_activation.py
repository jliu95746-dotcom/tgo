"""Activation and original receipt observation share actual owned state."""

import json

import pytest
from agno.tools.function import FunctionCall

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent import Agent
from app.runtime.supervisor.agents.device_script_tools import (
    build_device_script_tools,
)
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent import AgentUpdate
from app.schemas.agent_activation import AgentActivationRequirements
from app.schemas.knowledge import KnowledgeChannel
from app.services import agent_activation_readiness as readiness_module
from app.services import agent_execution_reference as reference_module
from app.services import agent_device_script_trials as trial_module
from app.services.agent_activation_readiness import (
    AgentActivationReadinessService,
)
from app.services.agent_device_script_tool_runtime import (
    AgentDeviceScriptToolRuntime,
)
from app.services.agent_execution_guard import AgentExecutionGuard
from app.services.agent_execution_reference import reference_for
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from app.services.agent_service import AgentService
from tests.test_agent_device_script_cases import PRIVATE
from tests.test_agent_device_script_runtime import context_for

pytest_plugins = ("tests.test_agent_device_script_selection",)


@pytest.mark.asyncio
async def test_activation_last_run_and_original_receipt_use_fresh_state(
    selected_db, monkeypatch
):
    sessions, project, actor, agent, owner, start, *_ = selected_db

    async def requirements(query):
        definition = AgentActivationRequirements.model_construct(
            **query.model_dump(),
            required_resources=["model"],
            required_capabilities=["business_data_access"],
            knowledge_cases=[],
        )
        definition.requirements_revision = definition.definition_revision()
        return AgentActivationRequirements.model_validate_json(
            definition.model_dump_json()
        )

    owner.get_agent_activation_requirements = requirements
    original_trials = trial_module.AgentDeviceScriptTrialService
    monkeypatch.setattr(
        trial_module, "AgentDeviceScriptTrialService",
        lambda db: original_trials(db, owner, owner),
    )

    async def configuration(
        db, requested_project, requested_actor, identifier
    ):
        assert (requested_project, requested_actor, identifier) == (
            project, actor, agent
        )
        stored = await AgentService(db).get_agent(
            project, agent, enrich_resources=False
        )
        async with AIServiceClient(AgentService(db), project) as adapter:
            current = await adapter._convert_agent_with_associations(
                stored, allow_inactive_trial=True, resolve_model=False
            )
        current.model_owner_revision = "a" * 64
        return current

    def readiness(db):
        async def read_configuration(*args):
            return await configuration(db, *args)

        return AgentActivationReadinessService(
            db, owner=owner, configuration_reader=read_configuration,
        )

    def guard(db):
        async def runtime(*args):
            return await configuration(db, *args)

        return AgentExecutionGuard(
            db, readiness=readiness(db), runtime_reader=runtime
        )

    async def loaded(adapter, identifier, headers):
        assert identifier == str(agent)
        return await configuration(
            adapter._agent_service.db, project, actor, agent
        )

    monkeypatch.setattr(
        readiness_module, "AgentActivationReadinessService", readiness
    )
    monkeypatch.setattr(reference_module, "AgentExecutionGuard", guard)
    monkeypatch.setattr(AIServiceClient, "get_agent", loaded)
    async with sessions() as db:
        context, policy = await context_for(
            db, project, actor, agent, start=start, owner=owner
        )
        from app.schemas.agent_activation import (
            AgentActivationRequirementsQuery,
        )

        current_requirements = await requirements(
            AgentActivationRequirementsQuery(
                project_id=project, requested_by=actor,
                template_id="assistant", template_version=1,
                channel=KnowledgeChannel.INTERNAL,
            )
        )
        policy.template_id = "assistant"
        policy.requirements_revision = (
            current_requirements.requirements_revision
        )
        stored = await db.get(Agent, agent)
        stored.is_active = False
        stored.skills_enabled = False
        await db.commit()

        proof = await readiness(db).read(project, actor, agent)
        assert proof.can_enable and not proof.enabled and not proof.blockers
        assert proof.channels[0].status == "passed"
        enabled = await AgentService(db).update_agent(
            project, agent, AgentUpdate(is_active=True),
            requested_by=actor, enrich_resources=False,
        )
        assert enabled.is_active
        context.agent = await configuration(db, project, actor, agent)
        context.execution_reference = await reference_for(
            db, project, context.agent, KnowledgeChannel.INTERNAL
        )

    reference = context.execution_reference
    reader = reference_module.AgentExecutionReferenceReader(sessions)
    assert await reader(reference) == reference
    runtime = AgentDeviceScriptToolRuntime(sessions, owner, owner)
    async with track_execution_scope(reference, reader) as scope:
        (tool,) = await build_device_script_tools(
            context, definition_reader=runtime.definition,
            runner=runtime.execute,
        )
        for identity in ("first-business", "last-business", "last-business"):
            call = FunctionCall(
                function=tool, arguments={"label": PRIVATE}, call_id=identity
            )
            await call.aexecute()
            assert json.loads(call.result) == {
                "ready": True, "label": PRIVATE
            }
            await scope.check()
        # One trial and exactly two business dispatches.
        assert owner.posts == 3

        async with sessions() as db:
            proof = await readiness(db).read(project, actor, agent)
            assert not proof.can_enable and proof.enabled
            assert {item.code for item in proof.blockers} == {
                "script_capacity_exhausted"
            }
            with pytest.raises(ConflictError):
                await guard(db).prepare(
                    project, context.agent, KnowledgeChannel.INTERNAL
                )
        rejected = FunctionCall(
            function=tool, arguments={"label": PRIVATE},
            call_id="new-call-after-exhaustion",
        )
        await rejected.aexecute()
        assert "未成功执行" in rejected.result
        assert context.knowledge_evidence.tool_results[-1].success is False
        assert owner.posts == 3

        async with sessions() as db:
            with pytest.raises(ConflictError):
                await AgentService(db).update_agent(
                    project, agent, AgentUpdate(is_active=True),
                    requested_by=actor, enrich_resources=False,
                )
            await db.rollback()
            assert (await db.get(Agent, agent)).is_active

    with pytest.raises(ConflictError):
        await reader(reference)
    async with track_execution_scope(reference, reader) as scope:
        await scope.check()
        async with sessions() as db:
            stored = await db.get(Agent, agent)
            stored.is_active = False
            await db.commit()
        with pytest.raises(ExecutionRevoked):
            await scope.check()
    with pytest.raises(AuthorizationError):
        await reader(reference)
