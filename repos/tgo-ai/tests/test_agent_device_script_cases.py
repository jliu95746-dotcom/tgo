"""Versioned cases and actual result bodies survive without dispatch replay."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select

from app.config import settings
from app.exceptions import ConflictError, NotFoundError
from app.exceptions import ValidationError as RequestError
from app.models.agent_device_script_binding import AgentDeviceScriptBinding
from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.models.agent_device_script_evidence import AgentDeviceScriptEvidence
from app.schemas.agent_device_script_cases import ScriptCaseWrite
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_trials import ScriptTrialStart
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
)
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,  # noqa: F401
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401


PRIVATE = "private-customer-fixture"


@pytest.fixture(autouse=True)
def evidence_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "script_trial_evidence_key",
        SecretStr("fixture-script-evidence-" + "x" * 32),
    )


def command(binding, **changes):
    data = dict(
        request_id=uuid4(),
        case_id=None,
        expected_revision=0,
        binding_id=binding.id,
        binding_revision=binding.revision,
        title="日报结果核验",
        parameters={"label": PRIVATE},
        expected_fields={"ready": True, "label": PRIVATE},
    )
    data.update(changes)
    return ScriptCaseWrite.model_validate(data)


def service(db, owner):
    return AgentDeviceScriptCaseService(
        AgentDeviceScriptTrialService(db, owner, owner)
    )


@pytest.mark.asyncio
async def test_case_save_is_encrypted_versioned_and_does_not_execute(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    request = command(binding)
    async with sessions() as db:
        cases = service(db, owner)
        first = await cases.save(project, actor, agent, request)
        replay = await cases.save(project, actor, agent, request)
        assert first.id == replay.id and first.revision == 1
        body = await cases.get(project, actor, agent, first.id)
        assert body.values.parameters == {"label": PRIVATE}
        assert PRIVATE not in repr(body.values)
        row = await db.get(AgentDeviceScriptCase, first.id)
        assert PRIVATE not in row.ciphertext and first.case_digest
        second = await cases.save(
            project,
            actor,
            agent,
            command(
                binding,
                case_id=first.case_id,
                expected_revision=1,
                title="更新后的日报核验",
            ),
        )
        assert second.case_id == first.case_id and second.revision == 2
        assert (
            await cases.get(project, actor, agent, first.id)
        ).case.status == "superseded"
        page = await cases.list(project, actor, agent)
        assert len(page.items) == 1 and page.items[0].id == second.id
        with pytest.raises(ConflictError):
            await cases.save(
                project,
                actor,
                agent,
                command(
                    binding,
                    request_id=request.request_id,
                    title="改写原请求",
                ),
            )
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_competing_case_edits_preserve_one_immutable_next_version(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        first = await service(db, owner).save(
            project, actor, agent, command(binding)
        )

    async def edit(title):
        async with sessions() as db:
            return await service(db, owner).save(
                project,
                actor,
                agent,
                command(
                    binding,
                    case_id=first.case_id,
                    expected_revision=1,
                    title=title,
                ),
            )

    results = await asyncio.gather(
        edit("第二版甲"), edit("第二版乙"), return_exceptions=True
    )
    assert sum(isinstance(item, ConflictError) for item in results) == 1
    assert [
        item.revision
        for item in results
        if not isinstance(item, BaseException)
    ] == [2]
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_saved_case_retains_actual_result_after_device_body_is_gone(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        case = await cases.save(project, actor, agent, command(binding))
        control = ScriptTrialControl(
            request_id=uuid4(),
            binding_id=binding.id,
            binding_revision=binding.revision,
        )
        report = await cases.run(project, actor, agent, case.id, control)
        assert report.status == "matched" and not report.can_enable
        before = owner.gets
        owner.receipts.clear()
        evidence = await cases.evidence(project, actor, agent, report.id)
        assert evidence.status == "available" and not evidence.can_enable
        assert evidence.case.id == case.id
        assert evidence.values.expected_fields == {
            "ready": True,
            "label": PRIVATE,
        }
        assert evidence.result_fields == {"ready": True, "label": PRIVATE}
        assert PRIVATE not in repr(evidence)
        row = await db.scalar(select(AgentDeviceScriptEvidence))
        assert row.report_id == report.id and PRIVATE not in row.ciphertext
        replay = await cases.run(project, actor, agent, case.id, control)
        assert replay.id == report.id and owner.posts == 1
        assert owner.gets == before


@pytest.mark.asyncio
async def test_case_and_result_cannot_cross_project_agent_or_report_actor(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        case = await cases.save(project, actor, agent, command(binding))
        report = await cases.run(
            project,
            actor,
            agent,
            case.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        for other_project, other_agent in (
            (uuid4(), agent),
            (project, uuid4()),
        ):
            with pytest.raises(NotFoundError):
                await cases.get(other_project, actor, other_agent, case.id)
        with pytest.raises(NotFoundError):
            await cases.evidence(project, uuid4(), agent, report.id)
    assert owner.posts == 1


@pytest.mark.asyncio
async def test_superseded_or_stale_binding_case_cannot_start_new_execution(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        first = await cases.save(project, actor, agent, command(binding))
        await cases.save(
            project,
            actor,
            agent,
            command(
                binding,
                case_id=first.case_id,
                expected_revision=1,
            ),
        )
        with pytest.raises(ConflictError):
            await cases.run(
                project,
                actor,
                agent,
                first.id,
                ScriptTrialControl(
                    request_id=uuid4(),
                    binding_id=binding.id,
                    binding_revision=binding.revision,
                ),
            )
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_expired_case_body_is_purged_but_version_metadata_survives(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        case = await cases.save(project, actor, agent, command(binding))
        row = await db.get(AgentDeviceScriptCase, case.id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        assert await cases.purge_expired() == 1
        await db.refresh(row)
        assert row.ciphertext is None and row.cleared_at is not None
        assert (await cases.list(project, actor, agent)).items[0].status == (
            "expired"
        )
        with pytest.raises(ConflictError):
            await cases.get(project, actor, agent, case.id)


@pytest.mark.asyncio
async def test_saved_case_is_rejected_after_binding_revision_changes(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        saved = await cases.save(project, actor, agent, command(binding))
        current = await db.get(AgentDeviceScriptBinding, binding.id)
        current.revision += 1
        await db.commit()
        assert (
            await cases.get(project, actor, agent, saved.id)
        ).case.status == "changed"
        with pytest.raises(ConflictError):
            await cases.run(
                project,
                actor,
                agent,
                saved.id,
                ScriptTrialControl(
                    request_id=uuid4(),
                    binding_id=binding.id,
                    binding_revision=binding.revision,
                ),
            )
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_case_fields_must_match_local_review_without_exposing_values(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        with pytest.raises(RequestError) as error:
            await service(db, owner).save(
                project,
                actor,
                agent,
                command(
                    binding,
                    parameters={"unreviewed": PRIVATE},
                ),
            )
        assert PRIVATE not in str(error.value)
        assert await db.scalar(select(AgentDeviceScriptCase)) is None
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_readable_cases_fail_closed_with_a_placeholder_signing_key(
    trial_db,  # noqa: F811
    monkeypatch,
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    monkeypatch.setattr(settings, "secret_key", "your-secret-key")
    async with sessions() as db:
        with pytest.raises(ConflictError, match="签名密钥"):
            await service(db, owner).save(
                project, actor, agent, command(binding)
            )
        assert await db.scalar(select(AgentDeviceScriptCase)) is None
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_case_integrity_binds_metadata_and_encrypted_values(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        saved = await cases.save(project, actor, agent, command(binding))
        row = await db.get(AgentDeviceScriptCase, saved.id)
        row.title = "未经原保存请求修改"
        await db.commit()
        with pytest.raises(ConflictError, match="完整性"):
            await cases.get(project, actor, agent, saved.id)
    assert owner.posts == 0


@pytest.mark.asyncio
async def test_expired_actual_result_is_purged_without_erasing_audit(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        saved = await cases.save(project, actor, agent, command(binding))
        report = await cases.run(
            project,
            actor,
            agent,
            saved.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        row = await db.scalar(select(AgentDeviceScriptEvidence))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        assert await cases.purge_expired() == 1
        await db.refresh(row)
        assert row.ciphertext is None and row.cleared_at is not None
        assert row.report_id == report.id and row.result_digest
        evidence = await cases.evidence(project, actor, agent, report.id)
        assert evidence.status == "expired"
        assert evidence.values is None and evidence.result_fields is None
    assert owner.posts == 1


@pytest.mark.asyncio
async def test_new_case_versions_do_not_rewrite_original_execution_evidence(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        first = await cases.save(project, actor, agent, command(binding))
        report = await cases.run(
            project,
            actor,
            agent,
            first.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        second = await cases.save(
            project,
            actor,
            agent,
            command(
                binding,
                case_id=first.case_id,
                expected_revision=1,
                expected_fields={"ready": False, "label": "new-expectation"},
            ),
        )
        evidence = await cases.evidence(project, actor, agent, report.id)
        assert second.revision == 2 and evidence.case.revision == 1
        assert evidence.case.status == "superseded"
        assert evidence.values.expected_fields == {
            "ready": True,
            "label": PRIVATE,
        }
        assert evidence.result_fields == evidence.values.expected_fields
    assert owner.posts == 1


@pytest.mark.asyncio
async def test_adhoc_evidence_does_not_invent_retained_bodies(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        data = command(binding).model_dump(
            include=set(ScriptTrialStart.model_fields)
        )
        report = await cases.trials.start(
            project, actor, agent, ScriptTrialStart(**data)
        )
        evidence = await cases.evidence(project, actor, agent, report.id)
        assert evidence.status == "not_retained" and not evidence.can_enable
        assert evidence.case is None and evidence.values is None
        assert evidence.result_fields is None and evidence.captured_at is None
    assert owner.posts == 1
