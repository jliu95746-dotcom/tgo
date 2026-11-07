"""Runtime dispatch uses a real scope and a fixed original request."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.agent import Agent as StoredAgent
from app.models.agent_activation_policy import AgentActivationPolicy
from app.models.internal import Agent, AgentExecutionContext
from app.schemas.agent_device_script_source import ScriptRuntimeApproval
from app.schemas.agent_execution import AgentExecutionReference
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_device_script_runtime import AgentDeviceScriptRuntime
from app.services.agent_execution_reference import policy_revision
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from app.services.agent_device_script_selection import ScriptSelectionService
from app.services.agent_device_script_selection_identity import (
    selection_snapshot,
)
from app.services.agent_business_evidence_revision import (
    business_evidence_revision,
)
from app.services.agent_trial_fingerprint import configuration_fingerprint
from tests.test_agent_device_script_cases import PRIVATE, service
from tests.test_agent_device_script_selection import select_command
from tests.test_agent_device_script_selection import (
    approved_run as approved_run,
    binding_database as binding_database,
    selected_db as selected_db,
    trial_db as trial_db,
)


async def context_for(db, project, actor, agent, *, start=None, owner=None):
    now = datetime.now(timezone.utc)
    if start is not None:
        await ScriptSelectionService(service(db, owner)).save(
            project,
            actor,
            agent,
            select_command(start),
        )
    policy = AgentActivationPolicy(
        project_id=project,
        agent_id=agent,
        created_by=actor,
        updated_by=actor,
        channel="internal",
        template_id="business_assistant",
        template_version=1,
        revision=1,
        requirements_revision="e" * 64,
    )
    db.add(policy)
    stored = await db.get(StoredAgent, agent)
    stored.is_active = True
    await db.commit()
    runtime = Agent(
        id=agent,
        project_id=str(project),
        name="测试员工",
        model="fixture-model",
        created_at=now,
        updated_at=now,
        device_script_selection=await selection_snapshot(db, project, agent),
    )
    reference = AgentExecutionReference(
        project_id=project,
        agent_id=agent,
        channel=KnowledgeChannel.INTERNAL,
        configuration_fingerprint=configuration_fingerprint(runtime),
        policy_revision=policy_revision([policy], KnowledgeChannel.INTERNAL),
        business_evidence_revision=await business_evidence_revision(
            db,
            project,
            actor,
            agent,
        ),
    )
    return (
        AgentExecutionContext(
            agent=runtime,
            project_id=str(project),
            message="合成执行测试",
            request_id="synthetic-runtime-source",
            timeout=30,
            knowledge_channel=KnowledgeChannel.INTERNAL,
            execution_reference=reference,
            parent_workflow_execution_id=uuid4(),
        ),
        policy,
    )


def approval_for(context):
    selection = context.agent.device_script_selection
    assert selection is not None
    return ScriptRuntimeApproval(
        actor_id=selection.configured_by,
        report_id=selection.report_id,
        review_id=selection.review_id,
        selection_id=selection.id,
        selection_revision=selection.revision,
        selection_digest=selection.selection_digest,
    )


@pytest.mark.asyncio
async def test_runtime_retries_recover_the_same_invocation_and_guard_output(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    from app.exceptions import ExternalServiceError

    owner.failure = ExternalServiceError("device-control", "lost receipt")
    async with sessions() as db:
        context, policy = await context_for(
            db,
            project,
            actor,
            agent,
            start=start,
            owner=owner,
        )

        async def current(expected):
            return expected

        approval = approval_for(context)
        invocation = uuid4()
        async with track_execution_scope(context.execution_reference, current):
            runtime = AgentDeviceScriptRuntime(
                service(db, owner),
                context,
                approval,
                invocation,
            )
            first = await runtime.execute({"label": PRIVATE})
            assert first.status == "succeeded"
            again = await runtime.execute({"label": PRIVATE})
            assert first.request_id == again.request_id
            assert again.result_fields == first.result_fields
            assert owner.posts == 2
            # The caller cannot convert a second input into a fresh request.
            with pytest.raises(ExecutionRevoked):
                await runtime.execute({"label": "different-input"})
            assert owner.posts == 2


@pytest.mark.asyncio
async def test_runtime_cannot_use_a_scope_owned_by_a_different_channel_admin(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        context, policy = await context_for(
            db,
            project,
            actor,
            agent,
            start=start,
            owner=owner,
        )
        policy.updated_by = uuid4()
        await db.commit()

        async def current(expected):
            return expected

        async with track_execution_scope(context.execution_reference, current):
            runtime = AgentDeviceScriptRuntime(
                service(db, owner),
                context,
                approval_for(context),
                uuid4(),
            )
            with pytest.raises(ExecutionRevoked):
                await runtime.execute({"label": PRIVATE})
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_revocation_after_device_result_prevents_runtime_publication(
    selected_db,
    monkeypatch,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        context, _ = await context_for(
            db,
            project,
            actor,
            agent,
            start=start,
            owner=owner,
        )
        allowed = True

        async def current(expected):
            if not allowed:
                raise ValueError("synthetic authorization revoked")
            return expected

        async def revoke(call):
            nonlocal allowed
            allowed = False

        monkeypatch.setattr(owner, "after_post", revoke)
        async with track_execution_scope(context.execution_reference, current):
            runtime = AgentDeviceScriptRuntime(
                service(db, owner),
                context,
                approval_for(context),
                uuid4(),
            )
            with pytest.raises(ExecutionRevoked):
                await runtime.execute({"label": PRIVATE})
        assert owner.posts == 2


@pytest.mark.asyncio
async def test_runtime_refuses_missing_scope_and_expression_only_context(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        context, _ = await context_for(
            db,
            project,
            actor,
            agent,
            start=start,
            owner=owner,
        )
        runtime = AgentDeviceScriptRuntime(
            service(db, owner),
            context,
            approval_for(context),
            uuid4(),
        )
        with pytest.raises(ExecutionRevoked):
            await runtime.execute({"label": PRIVATE})
        assert owner.posts == 1

        async def current(expected):
            return expected

        for changes in (
            {"execution_reference": None},
            {"response_purpose": "expression"},
            {"disable_tools": True},
            {
                "knowledge_channel": KnowledgeChannel.WEB,
                "execution_reference": context.execution_reference.model_copy(
                    update={"channel": KnowledgeChannel.WEB},
                ),
            },
        ):
            candidate = context.model_copy(update=changes)
            async with track_execution_scope(
                candidate.execution_reference,
                current,
            ):
                adapter = AgentDeviceScriptRuntime(
                    service(db, owner),
                    candidate,
                    runtime.approval,
                    uuid4(),
                )
                with pytest.raises(ExecutionRevoked):
                    await adapter.execute({"label": PRIVATE})
            assert owner.posts == 1
        async with track_execution_scope(context.execution_reference, current):
            adapter = AgentDeviceScriptRuntime(
                service(db, owner),
                context,
                runtime.approval,
                uuid4(),
            )
            with pytest.raises(ExecutionRevoked) as error:
                await adapter.execute({"label": {"private": PRIVATE}})
            assert PRIVATE not in str(error.value) and owner.posts == 1
