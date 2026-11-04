"""A selected approval is configuration and revocable execution evidence."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.agent_activation_policy import AgentActivationPolicy
from app.models.agent_device_script_selection import AgentDeviceScriptSelection
from app.runtime.core.exceptions import TransformationError
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_device_script_selection import ScriptSelectionWrite
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_activation_guard import business_fingerprint
from app.services.agent_business_evidence_revision import (
    business_evidence_revision,
)
from app.services.agent_device_script_reviews import AgentDeviceScriptReviews
from app.services.agent_device_script_selection import ScriptSelectionService
from app.services.agent_execution_reference import reference_for
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    track_execution_scope,
)
from app.services.agent_service import AgentService
from app.services.agent_trial_fingerprint import (
    configuration_fingerprint,
    private_digest,
)
from tests.test_agent_device_script_selection import select_command, service

pytest_plugins = ("tests.test_agent_device_script_selection",)


async def load(db, project, agent):
    stored = await AgentService(db).get_agent(
        project, agent, enrich_resources=False
    )
    reader = AIServiceClient(AgentService(db), project)
    return stored, await reader._convert_agent_with_associations(
        stored,
        allow_inactive_trial=True,
        resolve_model=False,
    )


@pytest.mark.asyncio
async def test_selection_loads_from_owner_and_changes_configuration(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        stored, initial = await load(db, project, agent)
        # Employees without selection retain the exact historical fingerprint.
        legacy = initial.model_dump(
            mode="json",
            exclude={
                "name",
                "created_at",
                "updated_at",
                "is_default",
                "business_connections",
                "device_script_selection",
            },
        )
        assert configuration_fingerprint(initial) == private_digest(legacy)
        before = await business_fingerprint(db, stored)
        selected = await ScriptSelectionService(service(db, owner)).save(
            project,
            actor,
            agent,
            select_command(start),
        )
        _, current = await load(db, project, agent)
        assert current.device_script_selection == selected
        assert configuration_fingerprint(current) != configuration_fingerprint(
            initial
        )
        assert await business_fingerprint(db, stored) != before
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_tampered_selection_cannot_load_as_current_configuration(
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
        row = await db.get(AgentDeviceScriptSelection, selected.id)
        row.selection_digest = "f" * 64
        await db.commit()
        with pytest.raises(TransformationError):
            await load(db, project, agent)
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_clear_and_reselect_revoke_an_earlier_execution_reference(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        selections = ScriptSelectionService(service(db, owner))
        await selections.save(project, actor, agent, select_command(start))
        db.add(
            AgentActivationPolicy(
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
        )
        await db.commit()
        _, internal = await load(db, project, agent)
        before = await reference_for(
            db, project, internal, KnowledgeChannel.INTERNAL
        )
        assert before.business_evidence_revision is not None
        assert (
            before.business_evidence_revision
            == await business_evidence_revision(
                db,
                project,
                actor,
                agent,
            )
        )
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
        _, cleared = await load(db, project, agent)
        middle = await reference_for(
            db, project, cleared, KnowledgeChannel.INTERNAL
        )
        await selections.save(project, actor, agent, select_command(start, 2))
        _, reselected = await load(db, project, agent)
        current = await reference_for(
            db, project, reselected, KnowledgeChannel.INTERNAL
        )
        assert (
            len(
                {
                    before.configuration_fingerprint,
                    middle.configuration_fingerprint,
                    current.configuration_fingerprint,
                }
            )
            == 3
        )
        assert (
            len(
                {
                    before.business_evidence_revision,
                    middle.business_evidence_revision,
                    current.business_evidence_revision,
                }
            )
            == 3
        )
        async with track_execution_scope(
            before, AsyncMock(return_value=current)
        ) as scope:
            with pytest.raises(ExecutionRevoked):
                await scope.check()
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_revocation_and_expiry_change_only_evidence(
    selected_db,
    monkeypatch,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        cases = service(db, owner)
        selected = await ScriptSelectionService(cases).save(
            project,
            actor,
            agent,
            select_command(start),
        )
        _, internal = await load(db, project, agent)
        fingerprint = configuration_fingerprint(internal)
        current = await business_evidence_revision(db, project, actor, agent)
        assert current is not None

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return selected.expires_at + timedelta(seconds=1)

        import app.services.agent_device_script_selection as selection_module

        with monkeypatch.context() as clock:
            clock.setattr(selection_module, "datetime", ExpiredClock)
            assert (
                await business_evidence_revision(db, project, actor, agent)
                != current
            )
        assert (
            await business_evidence_revision(db, project, actor, agent)
            == current
        )
        await AgentDeviceScriptReviews(cases).revoke(
            project,
            actor,
            agent,
            start.report_id,
            start.review_id,
        )
        assert (
            await business_evidence_revision(db, project, actor, agent)
            != current
        )
        _, after = await load(db, project, agent)
        assert configuration_fingerprint(after) == fingerprint
        assert owner.posts == 1
