"""Approval selection never follows later reports or repeats a saved write."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from app.exceptions import ConflictError, NotFoundError
from app.models.agent import Agent
from app.models.agent_device_script_selection import (
    AgentDeviceScriptSelection,
    AgentDeviceScriptSelectionRequest,
)
from sqlalchemy import select
from app.schemas.agent_device_script_selection import ScriptSelectionWrite
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_reviews import ScriptReviewWrite
from app.services.agent_device_script_selection import ScriptSelectionService
from app.services.agent_device_script_reviews import AgentDeviceScriptReviews
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,
)
from tests.test_agent_device_script_cases import (  # noqa: F401
    command,
    evidence_key,
    service,
)
from tests.test_agent_device_script_runs import approved_run  # noqa: F401
from tests.test_agent_device_script_trials import trial_db  # noqa: F401


@pytest_asyncio.fixture
async def selected_db(approved_run):  # noqa: F811
    *_, migrate, engine = approved_run
    async with engine.begin() as connection:
        for action in ("upgrade", "downgrade", "upgrade"):
            await connection.run_sync(
                migrate,
                "o3d4e5f6a7b8_script_selections.py",
                action,
            )
    yield approved_run


def select_command(start, revision=0):
    return ScriptSelectionWrite(
        request_id=uuid4(),
        expected_revision=revision,
        report_id=start.report_id,
        review_id=start.review_id,
    )


@pytest.mark.asyncio
async def test_selection_replay_and_clear_preserve_the_original_receipt(
    selected_db,
):
    (
        sessions,
        project,
        actor,
        agent,
        owner,
        start,
        _,
        migrate,
        engine,
    ) = selected_db
    command = select_command(start)
    async with sessions() as db:
        selections = ScriptSelectionService(service(db, owner))
        original = await selections.save(project, actor, agent, command)
        replay = await selections.save(project, actor, agent, command)
        assert original == replay and original.revision == 1
        state = await selections.read(project, actor, agent)
        assert state.status == "selected" and state.can_enable is False
        assert state.selection.report_id == start.report_id
        clear = ScriptSelectionWrite(
            request_id=uuid4(),
            expected_revision=1,
            enabled=False,
        )
        removed = await selections.save(project, actor, agent, clear)
        assert not removed.enabled and removed.revision == 2
        assert (
            await selections.read(project, actor, agent)
        ).status == "unselected"
        assert (
            await selections.request_result(
                project,
                actor,
                agent,
                command.request_id,
            )
            == original
        )
        assert (
            await selections.save(project, actor, agent, command) == original
        )
        assert (
            await selections.read(project, actor, agent)
        ).selection == removed
        with pytest.raises(ConflictError):
            await selections.save(
                project,
                actor,
                agent,
                command.model_copy(update={"review_id": uuid4()}),
            )
        assert owner.posts == 1
    with pytest.raises(RuntimeError, match="selection history exists"):
        async with engine.begin() as connection:
            await connection.run_sync(
                migrate,
                "o3d4e5f6a7b8_script_selections.py",
                "downgrade",
            )


@pytest.mark.asyncio
async def test_scope_active_employee_and_stale_revision_cannot_select(
    selected_db,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        selections = ScriptSelectionService(service(db, owner))
        command = select_command(start)
        for changes in ({"review_id": uuid4()}, {"expected_revision": 1}):
            with pytest.raises((ConflictError, NotFoundError)):
                await selections.save(
                    project,
                    actor,
                    agent,
                    command.model_copy(update=changes),
                )
        with pytest.raises(NotFoundError):
            await selections.request_result(project, uuid4(), agent, uuid4())
        employee = await db.get(Agent, agent)
        employee.is_active = True
        await db.commit()
        with pytest.raises(ConflictError, match="停用"):
            await selections.save(project, actor, agent, command)
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_revoked_approval_is_unusable_and_stored_selection_is_signed(
    selected_db,
    monkeypatch,
):
    sessions, project, actor, agent, owner, start, *_ = selected_db
    async with sessions() as db:
        cases = service(db, owner)
        selections = ScriptSelectionService(cases)
        original = await selections.save(
            project,
            actor,
            agent,
            select_command(start),
        )

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return original.expires_at + timedelta(seconds=1)

        import app.services.agent_device_script_selection as selection_module

        with monkeypatch.context() as clock:
            clock.setattr(selection_module, "datetime", ExpiredClock)
            assert (
                await selections.read(project, actor, agent)
            ).status == "expired"
        await AgentDeviceScriptReviews(cases).revoke(
            project,
            actor,
            agent,
            start.report_id,
            start.review_id,
        )
        assert (
            await selections.read(project, actor, agent)
        ).status == "revoked"
        with pytest.raises(ConflictError):
            await selections.save(
                project,
                actor,
                agent,
                select_command(start, 1),
            )
        row = await db.get(AgentDeviceScriptSelection, original.id)
        row.selection_digest = "f" * 64
        await db.commit()
        with pytest.raises(ConflictError, match="核验"):
            await selections.read(project, actor, agent)
        request = await db.scalar(
            select(
                AgentDeviceScriptSelectionRequest,
            ).where(
                AgentDeviceScriptSelectionRequest.request_id
                == original.request_id,
            )
        )
        request.result_digest = "f" * 64
        await db.commit()
        with pytest.raises(ConflictError, match="核验"):
            await selections.request_result(
                project,
                actor,
                agent,
                original.request_id,
            )
        assert owner.posts == 1


@pytest.mark.asyncio
async def test_a_later_approved_case_does_not_replace_the_selected_report(
    selected_db,
):
    sessions, project, actor, agent, owner, start, approved, *_ = selected_db
    async with sessions() as db:
        cases = service(db, owner)
        selections = ScriptSelectionService(cases)
        chosen = await selections.save(
            project,
            actor,
            agent,
            select_command(start),
        )
        old = await cases._version(
            project,
            agent,
            approved.review.case_version_id,
        )
        binding = await cases.trials.bindings._binding(project, agent)
        revised = await cases.save(
            project,
            actor,
            agent,
            command(
                binding,
                case_id=old.case_id,
                expected_revision=old.revision,
            ),
        )
        result = await cases.run(
            project,
            actor,
            agent,
            revised.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        newer = await AgentDeviceScriptReviews(cases).decide(
            project,
            actor,
            agent,
            result.id,
            ScriptReviewWrite(
                case_version_id=revised.id,
                case_digest=revised.case_digest,
                result_digest=result.result_digest,
                decision="approved",
            ),
        )
        state = await selections.read(project, actor, agent)
        assert state.status == "changed" and state.selection == chosen
        explicit = ScriptSelectionWrite(
            request_id=uuid4(),
            expected_revision=1,
            report_id=result.id,
            review_id=newer.review.id,
        )
        updated = await selections.save(project, actor, agent, explicit)
        assert updated.report_id == result.id and updated.revision == 2
        assert (
            await selections.read(project, actor, agent)
        ).status == "selected"
        assert owner.posts == 2
