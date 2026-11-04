"""Unselected or mismatched approvals never reach the device adapter."""

from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import datetime, timedelta

import pytest

from app.schemas.agent_device_script_source import ScriptRuntimeApproval
from app.models.agent import Agent as StoredAgent
from app.schemas.agent_device_script_selection import ScriptSelectionWrite
from app.services.agent_business_evidence_revision import (
    business_evidence_revision,
)
from app.services.agent_device_script_runtime import AgentDeviceScriptRuntime
from app.services.agent_device_script_selection import ScriptSelectionService
from app.services.agent_device_script_reviews import AgentDeviceScriptReviews
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from app.services.agent_trial_fingerprint import configuration_fingerprint
from tests.test_agent_device_script_cases import PRIVATE, service
from tests.test_agent_device_script_runtime import approval_for, context_for
from tests.test_agent_device_script_selection import select_command

pytest_plugins = ("tests.test_agent_device_script_selection",)


@pytest.mark.asyncio
async def test_unselected_approval_is_not_a_runtime_grant(selected_db):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        context, _ = await context_for(db, project, actor, agent)
        approval = ScriptRuntimeApproval.model_construct(
            actor_id=actor,
            report_id=start.report_id,
            review_id=start.review_id,
            selection_id=uuid4(),
            selection_revision=1,
            selection_digest="f" * 64,
        )
        async with track_execution_scope(
            context.execution_reference,
            AsyncMock(
                side_effect=lambda reference: reference,
            ),
        ):
            adapter = AgentDeviceScriptRuntime(
                service(db, owner), context, approval, uuid4()
            )
            with pytest.raises(ExecutionRevoked):
                await adapter.execute({"label": PRIVATE})
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_runtime_requires_the_exact_selected_revision_and_digest(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        selected = await ScriptSelectionService(service(db, owner)).save(
            project,
            actor,
            agent,
            select_command(start),
        )
        context, _ = await context_for(db, project, actor, agent)
        context.agent.device_script_selection = selected
        context.execution_reference = context.execution_reference.model_copy(
            update={
                "configuration_fingerprint": configuration_fingerprint(
                    context.agent
                ),
                "business_evidence_revision": await business_evidence_revision(
                    db,
                    project,
                    actor,
                    agent,
                ),
            }
        )
        for changes in (
            {"selection_id": uuid4()},
            {"selection_revision": 2},
            {"selection_digest": "f" * 64},
            {"report_id": uuid4()},
            {"review_id": uuid4()},
        ):
            approval = ScriptRuntimeApproval.model_construct(
                **{
                    "actor_id": actor,
                    "report_id": start.report_id,
                    "review_id": start.review_id,
                    "selection_id": selected.id,
                    "selection_revision": selected.revision,
                    "selection_digest": selected.selection_digest,
                    **changes,
                }
            )
            async with track_execution_scope(
                context.execution_reference,
                AsyncMock(
                    side_effect=lambda reference: reference,
                ),
            ):
                adapter = AgentDeviceScriptRuntime(
                    service(db, owner), context, approval, uuid4()
                )
                with pytest.raises(ExecutionRevoked):
                    await adapter.execute({"label": PRIVATE})
            assert owner.posts == 1


@pytest.mark.asyncio
async def test_cleared_reselected_or_expired_selection_revokes_runtime(
    selected_db,
    monkeypatch,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        cases = service(db, owner)
        context, _ = await context_for(
            db,
            project,
            actor,
            agent,
            start=start,
            owner=owner,
        )
        approval = approval_for(context)

        async def denied():
            async with track_execution_scope(
                context.execution_reference,
                AsyncMock(
                    side_effect=lambda reference: reference,
                ),
            ):
                adapter = AgentDeviceScriptRuntime(
                    cases, context, approval, uuid4()
                )
                with pytest.raises(ExecutionRevoked):
                    await adapter.execute({"label": PRIVATE})
            assert owner.posts == 1

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return (
                    context.agent.device_script_selection.expires_at
                    + timedelta(seconds=1)
                )

        import app.services.agent_device_script_selection as selection_module

        with monkeypatch.context() as clock:
            clock.setattr(selection_module, "datetime", ExpiredClock)
            await denied()
        # Direct fixture changes simulate a serving-state transition; no public
        # activation route is opened by the runtime tests.
        stored = await db.get(StoredAgent, agent)
        stored.is_active = False
        await db.commit()
        selections = ScriptSelectionService(cases)
        await selections.save(
            project,
            actor,
            agent,
            ScriptSelectionWrite(
                request_id=uuid4(),
                expected_revision=1,
                enabled=False,
            ),
        )
        stored.is_active = True
        await db.commit()
        await denied()
        stored.is_active = False
        await db.commit()
        await selections.save(project, actor, agent, select_command(start, 2))
        stored.is_active = True
        await db.commit()
        await denied()


@pytest.mark.asyncio
async def test_revoking_selected_review_after_result_blocks_publication(
    selected_db,
    monkeypatch,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        cases = service(db, owner)
        context, _ = await context_for(
            db,
            project,
            actor,
            agent,
            start=start,
            owner=owner,
        )

        async def revoke(call):
            await AgentDeviceScriptReviews(cases).revoke(
                project,
                actor,
                agent,
                start.report_id,
                start.review_id,
            )

        monkeypatch.setattr(owner, "after_post", revoke)
        async with track_execution_scope(
            context.execution_reference,
            AsyncMock(
                side_effect=lambda reference: reference,
            ),
        ):
            adapter = AgentDeviceScriptRuntime(
                cases, context, approval_for(context), uuid4()
            )
            with pytest.raises(ExecutionRevoked):
                await adapter.execute({"label": PRIVATE})
        assert owner.posts == 2
