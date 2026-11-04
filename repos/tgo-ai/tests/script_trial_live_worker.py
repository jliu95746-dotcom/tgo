"""Actual trial service and HTTP client in an owned PostgreSQL schema."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from datetime import datetime, timezone

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.models import Agent, BaseModel
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.schemas.agent_device_script_binding import AgentDeviceScriptWrite
from app.schemas.agent_device_script_trials import ScriptTrialStart
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_cases import ScriptCaseWrite
from app.models.agent_device_script_evidence import AgentDeviceScriptEvidence
from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_bindings import (
    AgentDeviceScriptBindingService,
)
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
)
from app.services.agent_device_script_requests import ScriptTrialRequests
from app.services.device_control_client import device_control_client


class FixtureActor:
    async def get_agent_configuration_actor(self, query):
        return AgentConfigurationActor(
            project_id=query.project_id,
            requested_by=query.requested_by,
            checked_at=datetime.now(timezone.utc),
        )


async def run_trial(sessions, project, actor, employee, request):
    async with sessions() as db:
        return await AgentDeviceScriptTrialService(
            db, FixtureActor(), device_control_client
        ).start(project, actor, employee, request)


async def cancel_running_trial(
    sessions, project, actor, employee, request, progress
):
    operation = asyncio.create_task(
        run_trial(sessions, project, actor, employee, request)
    )
    control = ScriptTrialControl.model_validate(
        request.model_dump(include=set(ScriptTrialControl.model_fields))
    )
    try:
        async with asyncio.timeout(20):
            while True:
                try:
                    if int(progress.read_text()) >= 2:
                        break
                except (FileNotFoundError, ValueError):
                    pass
                if operation.done():
                    await operation
                    raise AssertionError("Trial ended before cancellation")
                await asyncio.sleep(0.02)
        async with sessions() as db:
            state = await ScriptTrialRequests(
                AgentDeviceScriptTrialService(
                    db, FixtureActor(), device_control_client
                )
            ).cancel(project, actor, employee, control)
        assert state.stop_status in {"requested", "confirmed"}
        first = await asyncio.wait_for(operation, 20)
        async with asyncio.timeout(10):
            while True:
                async with sessions() as db:
                    state = await ScriptTrialRequests(
                        AgentDeviceScriptTrialService(
                            db, FixtureActor(), device_control_client
                        )
                    ).state(project, actor, employee, request.request_id)
                if state.stop_status == "confirmed":
                    break
                await asyncio.sleep(0.05)
        assert state.report.id == first.id
        assert first.status == "failed" and first.error_code == "interrupted"
        return first, state
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def main():
    if os.getenv("TGO_SCRIPT_AI_LOOPBACK_TEST") != "owned-runtime-only":
        raise RuntimeError("Requires explicit owned loopback runtime")
    fixture_path = Path(sys.argv[1]).resolve(strict=True)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    url = urlsplit(fixture["url"])
    assert url.scheme == "http" and url.hostname == "127.0.0.1"
    assert url.port and not (
        url.username or url.password or url.query or url.fragment
    )
    settings.secret_key = fixture["test_secret"]
    settings.device_control_service_url = fixture["url"]
    schema = "script_trial_live_" + uuid4().hex
    assert re.fullmatch(r"script_trial_live_[a-f0-9]{32}", schema)
    admin = create_async_engine(settings.database_url, hide_parameters=True)
    assert (
        admin.url.host in {"127.0.0.1", "localhost"}
        and admin.url.port == 15432
    )
    engine = create_async_engine(
        settings.database_url,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    created = False
    result = None
    try:
        async with admin.begin() as db:
            await db.execute(CreateSchema(schema))
        created = True
        async with engine.begin() as db:
            assert await db.scalar(text("SELECT current_schema()")) == schema
            excluded = {
                "ai_agent_device_script_bindings",
                "ai_agent_device_script_requests",
                "ai_agent_device_script_trials",
                "ai_agent_device_script_trial_requests",
                "ai_agent_device_script_cases",
                "ai_agent_device_script_evidence",
                "ai_agent_device_script_case_saves",
                "ai_agent_device_script_reviews",
                "ai_agent_device_script_runs",
                "ai_agent_device_script_selections",
                "ai_agent_device_script_selection_requests",
            }
            assert all(
                table.schema in {None, schema}
                for table in BaseModel.metadata.tables.values()
            )
            await db.run_sync(
                lambda conn: BaseModel.metadata.create_all(
                    conn,
                    tables=[
                        table
                        for name, table in BaseModel.metadata.tables.items()
                        if name not in excluded
                    ],
                )
            )
            for revision in (
                "g5b6c7d8e9f0_agent_device_script_bindings",
                "h6c7d8e9f0a1_agent_device_script_trials",
                "i7d8e9f0a1b2_script_trial_requests",
                "j8e9f0a1b2c3_script_case_evidence",
                "k9f0a1b2c3d4_script_case_save_requests",
            ):
                path = (
                    Path(__file__).parents[1]
                    / "migrations/versions"
                    / (revision + ".py")
                )
                spec = importlib.util.spec_from_file_location(
                    "owned_" + revision, path
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                def upgrade(conn):
                    with Operations.context(MigrationContext.configure(conn)):
                        module.upgrade()

                await db.run_sync(upgrade)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        project = UUID(fixture["policy"]["project_id"])
        employee, device, actor = (
            UUID(fixture["agent_id"]),
            UUID(fixture["policy"]["device_id"]),
            uuid4(),
        )
        async with sessions() as db:
            db.add(
                Agent(
                    id=employee,
                    project_id=project,
                    name="隔离脚本试验",
                    model="fixture",
                    is_active=False,
                    skills_enabled=False,
                )
            )
            await db.commit()
            binding = await AgentDeviceScriptBindingService(
                db, FixtureActor(), device_control_client
            ).save(
                project,
                actor,
                employee,
                AgentDeviceScriptWrite(
                    request_id=uuid4(),
                    expected_revision=0,
                    device_id=device,
                    expected_policy_digest=fixture["policy"]["policy"][
                        "digest"
                    ],
                ),
            )
        request = ScriptTrialStart(
            request_id=uuid4(),
            binding_id=binding.id,
            binding_revision=binding.revision,
            parameters={"label": fixture["label"]},
            expected_fields={"ready": True, "label": fixture["label"]},
        )
        stop_state = None
        saved_case = None
        control = ScriptTrialControl.model_validate(
            request.model_dump(include=set(ScriptTrialControl.model_fields))
        )
        if fixture.get("saved_case"):
            async with sessions() as db:
                cases = AgentDeviceScriptCaseService(
                    AgentDeviceScriptTrialService(
                        db, FixtureActor(), device_control_client
                    )
                )
                saved_case = await cases.save(
                    project,
                    actor,
                    employee,
                    ScriptCaseWrite(
                        **request.model_dump(),
                        case_id=None,
                        expected_revision=0,
                        title="真实 Windows 只读脚本结果核验",
                    ),
                )
                save_state = await cases.save_state(
                    project, actor, employee, request.request_id
                )
                assert (
                    save_state.status == "saved"
                    and save_state.case.id == saved_case.id
                    and not save_state.can_enable
                )
                first = await cases.run(
                    project, actor, employee, saved_case.id, control
                )
        elif fixture.get("cancel_progress"):
            progress = Path(fixture["cancel_progress"]).resolve()
            assert progress.parent == fixture_path.parent
            assert progress.name == "running-progress"
            first, stop_state = await cancel_running_trial(
                sessions, project, actor, employee, request, progress
            )
        else:
            first = await run_trial(
                sessions, project, actor, employee, request
            )
        expected_status = "failed" if stop_state else "matched"
        assert first.status == expected_status and not first.can_enable
        assert first.remaining_runs == 0
        async with sessions() as db:
            trials = AgentDeviceScriptTrialService(
                db, FixtureActor(), device_control_client
            )
            if saved_case:
                cases = AgentDeviceScriptCaseService(trials)
                again = await cases.run(
                    project, actor, employee, saved_case.id, control
                )
                evidence = await cases.evidence(
                    project, actor, employee, first.id
                )
                assert (
                    evidence.status == "available" and not evidence.can_enable
                )
                assert (
                    evidence.case.id == saved_case.id
                    and evidence.case.revision == 1
                )
                assert (
                    evidence.values.expected_fields == request.expected_fields
                )
                assert evidence.result_fields == request.expected_fields
                for model in (
                    AgentDeviceScriptCase,
                    AgentDeviceScriptEvidence,
                ):
                    stored = await db.scalar(select(model))
                    assert (
                        stored.ciphertext
                        and fixture["label"] not in stored.ciphertext
                    )
            else:
                again = await trials.start(project, actor, employee, request)
            recovered = await AgentDeviceScriptTrialService(
                db, FixtureActor(), device_control_client
            ).by_request(project, actor, employee, request.request_id)
            row = await db.scalar(select(AgentDeviceScriptTrial))
            assert (
                first.id == again.id == recovered.id
                and recovered.status == expected_status
            )
            assert fixture["label"] not in row.reference_json
            ref = json.loads(row.reference_json)
            assert (
                "parameters_json" not in ref and "expected_fields" not in ref
            )
            assert not (await db.get(Agent, employee)).is_active
            result = dict(
                same_request=True,
                comparison=first.status,
                execution_id=str(first.execution_id),
                session_id=ref["identity"]["session_id"],
            )
            if stop_state:
                result["stop_status"] = stop_state.stop_status
            if saved_case:
                result["evidence_retained"] = True
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as db:
                await db.execute(DropSchema(schema, cascade=True))
                assert (
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM pg_namespace "
                            "WHERE nspname=:name"
                        ),
                        {"name": schema},
                    )
                    == 0
                )
        await admin.dispose()
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
