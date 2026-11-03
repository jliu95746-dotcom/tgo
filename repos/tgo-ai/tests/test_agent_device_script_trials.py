"""A paused employee trial persists before I/O and cannot replay its POST."""

import asyncio
import importlib.util
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, inspect

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.exceptions import ValidationError as RequestError
from app.models.agent import Agent
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.models.agent_device_script_binding import AgentDeviceScriptBinding
from app.schemas.agent_device_script_trials import ScriptTrialStart
from app.schemas.device_script_budget import ScriptBudgetRead
from app.schemas.device_script_execution import ScriptExecutionRead
from app.schemas.device_script_policy import ScriptParameter
from app.services.agent_device_script_bindings import (
    AgentDeviceScriptBindingService,
)
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
)
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,  # noqa: F401
    Owner,
    write_command,
)


class Device(Owner):
    def __init__(self):
        super().__init__()
        self.posts = 0
        self.gets = 0
        self.receipts = {}
        self.connection = "c" * 32
        self.before_post = None
        self.after_post = None
        self.failure = None
        self.wait = None

    async def validate_script_configuration(self, project, device):
        bound, item = await super().validate_script_configuration(
            project, device
        )
        field = ScriptParameter(
            name="label", kind="string", required=True, max_length=80
        )
        item.parameters = [field]
        item.result_fields.append(field)
        return bound, item

    async def read_script_budget(self, project, device, policy, grant):
        return ScriptBudgetRead(
            project_id=project,
            device_id=device,
            connection_instance_id=self.connection,
            session_id="d" * 32,
            policy_digest=policy,
            grant_digest=grant,
            policy_max_runs=3,
            grant_max_runs=3,
            server_policy_used=self.posts,
            server_grant_used=self.posts,
            device_policy_used=self.posts,
            device_grant_used=self.posts,
            remaining_runs=3 - self.posts,
            observed_at=datetime.now(timezone.utc),
        )

    async def start_session(self, identity):
        pass

    async def heartbeat_session(self, identity):
        pass

    async def finish_session(self, identity, status):
        pass

    async def execute_script(self, call):
        if self.before_post:
            await self.before_post(call)
        self.posts += 1
        if self.wait:
            await self.wait.wait()
        result = ScriptExecutionRead(
            device_id=call.identity.device_id,
            execution_id=call.execution_id,
            session_id=call.identity.session_id,
            agent_id=call.identity.agent_id,
            policy_digest=call.policy_digest,
            parameters_digest=call.parameters_digest,
            context_digest=call.context_digest,
            status="succeeded",
            failure="",
            result_available=True,
            fields={"ready": True, "label": "private-customer-fixture"},
            created_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        self.receipts[call.execution_id] = result
        if self.after_post:
            await self.after_post(call)
        if self.failure:
            raise ExternalServiceError("device-control", "unknown")
        return result

    async def read_script(self, reference):
        self.gets += 1
        assert not hasattr(reference, "parameters_json")
        if reference.execution_id not in self.receipts:
            raise ExternalServiceError("device-control", "unknown")
        return self.receipts[reference.execution_id]


@pytest_asyncio.fixture
async def trial_db(binding_database):  # noqa: F811
    sessions, project, actor, employee, device, _, engine = binding_database
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/h6c7d8e9f0a1_agent_device_script_trials.py"
    )
    spec = importlib.util.spec_from_file_location(
        "owned_trial_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    def migrate(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, action)()

    async with engine.begin() as db:
        for action in ("upgrade", "downgrade", "upgrade"):
            await db.run_sync(migrate, action)
        actual = await db.run_sync(
            lambda conn: inspect(conn).get_columns(
                "ai_agent_device_script_trials"
            )
        )
        assert {c["name"] for c in actual} == set(
            AgentDeviceScriptTrial.__table__.columns.keys()
        )

        def check_model(conn):
            table = AgentDeviceScriptTrial.__table__
            inspector = inspect(conn)
            columns = {c["name"]: c for c in inspector.get_columns(table.name)}
            for column in table.columns:
                assert columns[column.name]["nullable"] == column.nullable
                assert columns[column.name]["type"].compile(
                    dialect=conn.dialect
                ) == column.type.compile(dialect=conn.dialect)
            unique = {
                tuple(c["column_names"])
                for c in inspector.get_unique_constraints(table.name)
            }
            assert unique == {
                ("project_id", "actor_id", "request_id"),
                ("execution_id",),
            }
            assert {
                c["name"] for c in inspector.get_check_constraints(table.name)
            } == {
                c.name
                for c in table.constraints
                if c.__class__.__name__ == "CheckConstraint"
            }
            foreign = inspector.get_foreign_keys(table.name)
            assert {tuple(c["constrained_columns"]) for c in foreign} == {
                ("agent_id",),
                ("binding_id",),
            }
            assert all(
                not c.get("options", {}).get("ondelete") for c in foreign
            )
            assert {
                tuple(c["column_names"])
                for c in inspector.get_indexes(table.name)
                if not c.get("duplicates_constraint")
            } == {
                tuple(column.name for column in index.columns)
                for index in table.indexes
            }

        await db.run_sync(check_model)
    request_path = (
        Path(__file__).parents[1]
        / "migrations/versions/i7d8e9f0a1b2_script_trial_requests.py"
    )
    request_spec = importlib.util.spec_from_file_location(
        "owned_trial_request_migration", request_path
    )
    request_migration = importlib.util.module_from_spec(request_spec)
    request_spec.loader.exec_module(request_migration)

    def migrate_requests(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(request_migration, action)()

    async with engine.begin() as db:
        for action in ("upgrade", "downgrade", "upgrade"):
            await db.run_sync(migrate_requests, action)
    evidence_path = Path(__file__).parents[1] / (
        "migrations/versions/j8e9f0a1b2c3_script_case_evidence.py"
    )
    evidence_spec = importlib.util.spec_from_file_location(
        "owned_case_evidence_migration", evidence_path
    )
    evidence_migration = importlib.util.module_from_spec(evidence_spec)
    evidence_spec.loader.exec_module(evidence_migration)

    def migrate_evidence(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(evidence_migration, action)()

    async with engine.begin() as db:
        for action in ("upgrade", "downgrade", "upgrade"):
            await db.run_sync(migrate_evidence, action)
    saves_path = Path(__file__).parents[1] / (
        "migrations/versions/k9f0a1b2c3d4_script_case_save_requests.py"
    )
    saves_spec = importlib.util.spec_from_file_location(
        "owned_case_saves_migration", saves_path
    )
    saves_migration = importlib.util.module_from_spec(saves_spec)
    saves_spec.loader.exec_module(saves_migration)

    def migrate_saves(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(saves_migration, action)()

    async with engine.begin() as db:
        for action in ("upgrade", "downgrade", "upgrade"):
            await db.run_sync(migrate_saves, action)
    owner = Device()
    async with sessions() as db:
        binding = await AgentDeviceScriptBindingService(db, owner, owner).save(
            project,
            actor,
            employee,
            write_command(
                request_id=uuid4(), expected_revision=0, device_id=device
            ),
        )
    yield (
        sessions,
        project,
        actor,
        employee,
        device,
        owner,
        binding,
        migrate,
        engine,
    )


@pytest.mark.asyncio
async def test_input_snapshot_is_fixed_and_signer_changes_invalidate(
    trial_db, monkeypatch
):
    from app.config import settings

    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    request = command(binding)
    original = owner.validate_script_configuration
    changed = False

    async def mutate(project, device):
        nonlocal changed
        result = await original(project, device)
        if not changed:
            changed = True
            request.parameters["label"] = "changed-after-click"
            request.expected_fields["label"] = "changed-after-click"
        return result

    owner.validate_script_configuration = mutate

    async def inspect_call(call):
        assert (
            json.loads(call.parameters_json)["parameters"]["label"]
            == "private-customer-fixture"
        )

    owner.before_post = inspect_call
    async with sessions() as db:
        result = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, request
        )
    assert result.status == "matched"
    with monkeypatch.context() as patch:
        patch.setattr(settings, "secret_key", uuid4().hex + uuid4().hex)
        async with sessions() as db:
            old = await AgentDeviceScriptTrialService(db, owner, owner).get(
                project, actor, employee, result.id
            )
        assert old.status == "matched" and old.applicability == "changed"
    assert owner.posts == 1


@pytest.mark.asyncio
async def test_duplicate_and_competing_requests_have_one_dispatch(trial_db):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    owner.wait = asyncio.Event()
    request = command(binding)

    async def first():
        async with sessions() as db:
            return await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, request
            )

    task = asyncio.create_task(first())
    try:
        async with asyncio.timeout(30):
            while owner.posts == 0:
                await asyncio.sleep(0.02)
        async with sessions() as db:
            repeated = await AgentDeviceScriptTrialService(
                db, owner, owner
            ).start(project, actor, employee, request)
            assert repeated.status == "unknown" and owner.posts == 1
        async with sessions() as db:
            with pytest.raises(ConflictError):
                await AgentDeviceScriptTrialService(db, owner, owner).start(
                    project, actor, employee, command(binding)
                )
        changed = request.model_copy(deep=True)
        changed.expected_fields["ready"] = False
        async with sessions() as db:
            with pytest.raises(ConflictError):
                await AgentDeviceScriptTrialService(db, owner, owner).start(
                    project, actor, employee, changed
                )
    finally:
        owner.wait.set()
        finished = await task
    assert finished.id == repeated.id and finished.status == "matched"
    assert owner.posts == 1
    owner.wait.clear()
    cancelled_command = command(binding)

    async def interrupted():
        async with sessions() as db:
            return await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, cancelled_command
            )

    pending = asyncio.create_task(interrupted())
    try:
        async with asyncio.timeout(30):
            while owner.posts < 2:
                await asyncio.sleep(0.02)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with sessions() as db:
            recovered = await AgentDeviceScriptTrialService(
                db, owner, owner
            ).by_request(
                project, actor, employee, cancelled_command.request_id
            )
        assert recovered.status == "unknown" and not recovered.can_enable
        assert owner.posts == 2
    finally:
        owner.wait.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_invalid_scope_input_and_empty_budget_never_dispatch(trial_db):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    for field, value in [
        ("binding_id", uuid4()),
        ("binding_revision", 9),
        ("parameters", {}),
        ("expected_fields", {"ready": 1, "label": "private-customer-fixture"}),
    ]:
        request = command(binding).model_copy(update={field: value}, deep=True)
        async with sessions() as db:
            with pytest.raises((ConflictError, RequestError)):
                await AgentDeviceScriptTrialService(db, owner, owner).start(
                    project, actor, employee, request
                )
    owner.posts = 3
    async with sessions() as db:
        with pytest.raises(ConflictError):
            await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, command(binding)
            )
        assert (
            await db.scalar(
                select(func.count()).select_from(AgentDeviceScriptTrial)
            )
            == 0
        )
    assert owner.posts == 3
    owner.posts = 0
    async with sessions() as db:
        row = await db.get(Agent, employee)
        row.is_active = True
        await db.commit()
    async with sessions() as db:
        with pytest.raises(ConflictError):
            await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, command(binding)
            )
        row = await db.get(Agent, employee)
        row.is_active = False
        await db.commit()
    owner.denied = True
    async with sessions() as db:
        with pytest.raises(ExternalServiceError):
            await AgentDeviceScriptTrialService(db, owner, owner).start(
                project, actor, employee, command(binding)
            )
    assert owner.posts == 0
    owner.denied = False

    async def expire_before_post(identity):
        async with sessions() as db:
            row = await db.scalar(select(AgentDeviceScriptTrial))
            row.dispatch_deadline_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            await db.commit()

    owner.start_session = expire_before_post
    async with sessions() as db:
        expired = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, command(binding)
        )
    assert expired.status == "failed" and expired.error_code == "interrupted"
    assert owner.posts == 0


def command(binding, **changes):
    return ScriptTrialStart.model_validate(
        dict(
            request_id=changes.pop("request_id", uuid4()),
            binding_id=binding.id,
            binding_revision=binding.revision,
            parameters={"label": "private-customer-fixture"},
            expected_fields={
                "ready": True,
                "label": "private-customer-fixture",
            },
            **changes,
        )
    )


@pytest.mark.asyncio
async def test_trial_is_committed_before_io_and_replays_no_post(trial_db):
    (
        sessions,
        project,
        actor,
        employee,
        _,
        owner,
        binding,
        migrate,
        engine,
    ) = trial_db

    async def check(call):
        async with sessions() as db:
            row = await db.scalar(select(AgentDeviceScriptTrial))
            assert row is not None and row.execution_id == call.execution_id
            assert row.dispatch_claimed_at is not None
            assert "private-customer-fixture" not in row.reference_json

    owner.before_post = check
    request = command(binding)
    async with sessions() as db:
        result = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, request
        )
    assert result.status == "matched" and not result.can_enable
    assert result.assessment == "not_reviewed"
    assert owner.posts == 1
    async with sessions() as db:
        repeated = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, request
        )
        assert repeated.id == result.id
        row = await db.get(AgentDeviceScriptTrial, result.id)
        assert "private-customer-fixture" not in row.reference_json
        assert not (await db.get(Agent, employee)).is_active
        assert (
            await db.scalar(
                select(func.count()).select_from(AgentDeviceScriptTrial)
            )
            == 1
        )
    assert owner.posts == 1
    async with sessions() as db:
        employee_row = await db.get(Agent, employee)
        employee_row.is_active = True
        await db.commit()
    async with sessions() as db:
        historical = await AgentDeviceScriptTrialService(db, owner, owner).get(
            project, actor, employee, result.id
        )
    assert (
        historical.status == "matched"
        and historical.applicability == "current"
    )
    with pytest.raises(RuntimeError):
        async with engine.begin() as db:
            await db.run_sync(migrate, "downgrade")


@pytest.mark.asyncio
async def test_unknown_execution_recovers_reference_only(trial_db):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db
    owner.failure = "lost_reply"
    request = command(binding)
    async with sessions() as db:
        first = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, request
        )
    assert first.status == "unknown" and owner.posts == 1
    async with sessions() as db:
        recovered = await AgentDeviceScriptTrialService(
            db, owner, owner
        ).by_request(project, actor, employee, request.request_id)
    assert (
        recovered.status == "matched"
        and recovered.execution_id == first.execution_id
    )
    assert owner.posts == 1 and owner.gets == 1
    async with sessions() as db:
        with pytest.raises(NotFoundError):
            await AgentDeviceScriptTrialService(db, owner, owner).get(
                project, uuid4(), employee, first.id
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "mismatch",
        "connection_changed",
        "binding_changed",
        "missing_fields",
        "foreign_receipt",
    ],
)
async def test_trial_cannot_claim_match_without_current_evidence(
    trial_db, case
):
    sessions, project, actor, employee, _, owner, binding, _, _ = trial_db

    async def change(call):
        if case == "connection_changed":
            owner.connection = "f" * 32
        elif case == "binding_changed":
            async with sessions() as db:
                row = await db.get(AgentDeviceScriptBinding, binding.id)
                row.revision += 1
                await db.commit()
        elif case == "missing_fields":
            owner.receipts[call.execution_id].fields = {}
            owner.receipts[call.execution_id].result_available = False
        elif case == "foreign_receipt":
            owner.receipts[call.execution_id].device_id = uuid4()
        elif case == "mismatch":
            owner.receipts[call.execution_id].fields["ready"] = False

    owner.after_post = change
    async with sessions() as db:
        result = await AgentDeviceScriptTrialService(db, owner, owner).start(
            project, actor, employee, command(binding)
        )
    assert result.status == ("mismatched" if case == "mismatch" else "failed")
    assert not result.can_enable and owner.posts == 1
