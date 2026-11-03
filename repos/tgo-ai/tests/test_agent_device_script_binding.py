"""Configuration is durable and cannot stand in for a business approval."""

import asyncio
import importlib.util
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import (
    CheckConstraint,
    UniqueConstraint,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import settings
from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models import Agent, BaseModel
from app.models.agent_device_script_binding import (
    AgentDeviceScriptBinding,
    AgentDeviceScriptBindingRequest,
)
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.schemas.agent_device_script_binding import AgentDeviceScriptWrite
from app.schemas.device_script_execution import ScriptPolicyRead
from app.schemas.device_script_policy import (
    ScriptDescriptor,
    ScriptPolicy,
    script_policy_digest,
)
from app.services.agent_activation_readiness import (
    AgentActivationReadinessService,
)
from app.services.agent_device_script_bindings import (
    AgentDeviceScriptBindingService,
)


EXPECTED_POLICY = (
    "8b0217331801c096bf0624638bdf7b8136bf5393c5304c39bed24b21f1361733"
)


def write_command(**values):
    if values.get("enabled", True):
        values.setdefault("expected_policy_digest", EXPECTED_POLICY)
    return AgentDeviceScriptWrite(**values)


class Owner:
    def __init__(self):
        self.denied = False
        self.calls = 0

    async def get_agent_configuration_actor(self, query):
        return AgentConfigurationActor(
            project_id=query.project_id,
            requested_by=uuid4() if self.denied else query.requested_by,
            checked_at=datetime.now(timezone.utc),
        )

    async def validate_script_configuration(self, project, device):
        self.calls += 1
        descriptor = ScriptDescriptor(
            script_id="daily_report",
            title="已审核报表",
            version="v1",
            grant_digest="a" * 64,
            effect="read_only",
            timeout_seconds=10,
            max_output_bytes=4096,
            max_runs=3,
            parameters=[],
            result_fields=[
                {"name": "ready", "kind": "boolean", "required": True}
            ],
        )
        policy = ScriptPolicy.model_construct(
            protocol_version=1,
            revision=1,
            script_id="daily_report",
            grant_digest="a" * 64,
            timeout_seconds=10,
            max_output_bytes=4096,
            max_runs=3,
            digest="",
        )
        policy.digest = script_policy_digest(policy)
        return (
            ScriptPolicyRead(
                project_id=project,
                device_id=device,
                policy=policy,
                status="active",
                updated_at=datetime.now(timezone.utc),
            ),
            descriptor,
        )


@pytest.fixture
def owner():
    return Owner()


@pytest.mark.parametrize(
    "extra",
    [
        {"verified": True},
        {"policy_digest": "a" * 64},
        {"script_id": "shell"},
        {"expected_revision": True},
        {"enabled": "true"},
        {"expected_policy_digest": None},
        {"expected_policy_digest": "not-a-digest"},
        {"enabled": False, "device_id": str(uuid4())},
    ],
)
def test_command_cannot_supply_approval_or_ambiguous_scope(extra):
    data = dict(
        request_id=uuid4(),
        expected_revision=0,
        device_id=uuid4(),
        expected_policy_digest=EXPECTED_POLICY,
    )
    data.update(extra)
    with pytest.raises(ValidationError):
        AgentDeviceScriptWrite.model_validate(data)


@pytest_asyncio.fixture
async def database():
    if os.getenv("AI_SCRIPT_BINDING_DB_TEST") != "owned-loopback-schema-only":
        pytest.skip("Requires explicit owned loopback PostgreSQL schema")
    admin = create_async_engine(settings.database_url, hide_parameters=True)
    assert admin.url.host in {"127.0.0.1", "localhost"}
    assert admin.url.port == 15432
    schema = "agent_script_binding_test_" + uuid4().hex
    assert re.fullmatch(r"agent_script_binding_test_[a-f0-9]{32}", schema)
    engine = create_async_engine(
        settings.database_url,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/g5b6c7d8e9f0_agent_device_script_bindings.py"
    )
    spec = importlib.util.spec_from_file_location(
        "owned_script_binding_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection, operation):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, operation)()

    created = False
    try:
        async with admin.begin() as db:
            await db.execute(CreateSchema(schema))
        created = True
        async with engine.begin() as db:
            assert await db.scalar(text("SELECT current_schema()")) == schema
            await db.run_sync(
                lambda sync: BaseModel.metadata.create_all(
                    sync,
                    tables=[
                        table
                        for name, table in BaseModel.metadata.tables.items()
                        if name
                        not in {
                            "ai_agent_device_script_bindings",
                            "ai_agent_device_script_requests",
                            "ai_agent_device_script_trials",
                            "ai_agent_device_script_trial_requests",
                            "ai_agent_device_script_cases",
                            "ai_agent_device_script_evidence",
                            "ai_agent_device_script_case_saves",
                        }
                    ],
                )
            )
            for action in ("upgrade", "downgrade", "upgrade"):
                await db.run_sync(migrate, action)
            for model in (
                AgentDeviceScriptBinding,
                AgentDeviceScriptBindingRequest,
            ):
                columns = await db.run_sync(
                    lambda sync: inspect(sync).get_columns(model.__tablename__)
                )
                assert {item["name"] for item in columns} == set(
                    model.__table__.columns.keys()
                )
                assert {
                    item["name"]: item["nullable"] for item in columns
                } == {
                    column.name: column.nullable
                    for column in model.__table__.columns
                }

                def inspect_shape(sync):
                    inspector = inspect(sync)
                    table = model.__tablename__
                    return (
                        {
                            column["name"]: str(
                                column["type"].compile(dialect=sync.dialect)
                            )
                            for column in inspector.get_columns(table)
                        },
                        {
                            column.name: str(
                                column.type.compile(dialect=sync.dialect)
                            )
                            for column in model.__table__.columns
                        },
                        inspector.get_pk_constraint(table),
                        inspector.get_unique_constraints(table),
                        inspector.get_indexes(table),
                        inspector.get_foreign_keys(table),
                        inspector.get_check_constraints(table),
                    )

                (
                    actual,
                    expected,
                    primary,
                    uniques,
                    indexes,
                    foreign,
                    checks,
                ) = await db.run_sync(inspect_shape)
                assert actual == expected
                assert primary["constrained_columns"] == ["id"]
                assert {
                    (item["name"], tuple(item["column_names"]))
                    for item in uniques
                } == {
                    (item.name, tuple(column.name for column in item.columns))
                    for item in model.__table__.constraints
                    if isinstance(item, UniqueConstraint)
                }
                assert {
                    (item["name"], tuple(item["column_names"]))
                    for item in indexes
                    if not item.get("duplicates_constraint")
                } == {
                    (item.name, tuple(column.name for column in item.columns))
                    for item in model.__table__.indexes
                }
                assert len(foreign) == 1
                assert foreign[0]["constrained_columns"] == ["agent_id"]
                assert foreign[0]["referred_table"] == "ai_agents"
                assert foreign[0]["referred_columns"] == ["id"]
                assert not foreign[0].get("options", {}).get("ondelete")
                assert {item["name"] for item in checks} == {
                    item.name
                    for item in model.__table__.constraints
                    if isinstance(item, CheckConstraint)
                }
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        project, actor, employee, device = (uuid4() for _ in range(4))
        async with sessions() as db:
            db.add(
                Agent(
                    id=employee,
                    project_id=project,
                    name="隔离报表员工",
                    model="fixture",
                    is_active=False,
                    skills_enabled=False,
                )
            )
            await db.commit()
        yield sessions, project, actor, employee, device, migrate, engine
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as db:
                await db.execute(DropSchema(schema, cascade=True))
                assert not await db.scalar(
                    text("SELECT 1 FROM pg_namespace WHERE nspname=:name"),
                    {"name": schema},
                )
        await admin.dispose()


async def test_concurrent_replay_durable_retirement_and_activation_gate(
    database, owner
):
    sessions, project, actor, employee, device, migrate, engine = database
    command = write_command(
        request_id=uuid4(),
        expected_revision=0,
        device_id=device,
    )

    async def save():
        async with sessions() as db:
            return await AgentDeviceScriptBindingService(
                db, owner, owner
            ).save(
                project,
                actor,
                employee,
                command,
            )

    first, repeated = await asyncio.gather(save(), save())
    assert first == repeated and first.revision == 1 and not first.can_enable
    async with sessions() as db:
        stored = await db.get(Agent, employee)
        assert stored.bound_device_id is None and not stored.is_active
        assert await AgentActivationReadinessService(
            db
        )._unverified_dependencies(stored)
        service = AgentDeviceScriptBindingService(db, owner, owner)
        retired = await service.save(
            project,
            actor,
            employee,
            write_command(
                request_id=uuid4(),
                expected_revision=1,
                enabled=False,
            ),
        )
        assert retired.revision == 2 and not retired.enabled
        assert not await AgentActivationReadinessService(
            db
        )._unverified_dependencies(await db.get(Agent, employee))
    async with sessions() as db:
        service = AgentDeviceScriptBindingService(db, owner, owner)
        assert (
            await service.request_result(
                project, actor, employee, command.request_id
            )
            == first
        )
        assert not (
            await service.read(project, actor, employee)
        ).binding.enabled
        assert (
            await db.scalar(
                select(func.count()).select_from(
                    AgentDeviceScriptBindingRequest
                )
            )
            == 2
        )
    with pytest.raises(RuntimeError, match="records exist"):
        async with engine.begin() as db:
            await db.run_sync(migrate, "downgrade")


async def test_foreign_active_or_denied_configuration_has_no_write(
    database, owner
):
    sessions, project, actor, employee, device, _, _ = database
    command = write_command(
        request_id=uuid4(), expected_revision=0, device_id=device
    )
    async with sessions() as db:
        service = AgentDeviceScriptBindingService(db, owner, owner)
        with pytest.raises(NotFoundError):
            await service.save(uuid4(), actor, employee, command)
        owner.denied = True
        with pytest.raises(ExternalServiceError):
            await service.save(project, actor, employee, command)
        owner.denied = False
        agent = await db.get(Agent, employee)
        agent.is_active = True
        await db.commit()
        with pytest.raises(ConflictError):
            await service.save(project, actor, employee, command)
        assert not await db.scalar(select(AgentDeviceScriptBinding.id))
        assert owner.calls == 0


async def test_commit_failure_and_lost_reply_have_distinct_recovery(
    database, owner, monkeypatch
):
    from unittest.mock import AsyncMock

    sessions, project, actor, employee, device, _, _ = database
    command = write_command(
        request_id=uuid4(),
        expected_revision=0,
        device_id=device,
    )
    async with sessions() as db:
        monkeypatch.setattr(
            db,
            "commit",
            AsyncMock(side_effect=RuntimeError("controlled commit failure")),
        )
        with pytest.raises(RuntimeError, match="commit failure"):
            await AgentDeviceScriptBindingService(db, owner, owner).save(
                project, actor, employee, command
            )
    async with sessions() as db:
        assert not await db.scalar(select(AgentDeviceScriptBinding.id))
        assert not await db.scalar(select(AgentDeviceScriptBindingRequest.id))
        original = db.commit

        async def committed_reply_lost():
            await original()
            raise RuntimeError("controlled response lost")

        monkeypatch.setattr(db, "commit", committed_reply_lost)
        with pytest.raises(RuntimeError, match="response lost"):
            await AgentDeviceScriptBindingService(db, owner, owner).save(
                project, actor, employee, command
            )
    checked = owner.calls
    async with sessions() as db:
        service = AgentDeviceScriptBindingService(db, owner, owner)
        recovered = await service.request_result(
            project, actor, employee, command.request_id
        )
        assert recovered.revision == 1 and recovered.device_id == device
        assert (
            await service.save(project, actor, employee, command) == recovered
        )
        assert owner.calls == checked
        assert (
            await db.scalar(
                select(func.count()).select_from(
                    AgentDeviceScriptBindingRequest
                )
            )
            == 1
        )


async def test_different_commands_race_and_original_id_cannot_change(
    database, owner
):
    sessions, project, actor, employee, device, _, _ = database
    commands = [
        write_command(
            request_id=uuid4(),
            expected_revision=0,
            device_id=device if index == 0 else uuid4(),
        )
        for index in range(2)
    ]

    async def save(command):
        async with sessions() as db:
            try:
                return await AgentDeviceScriptBindingService(
                    db, owner, owner
                ).save(project, actor, employee, command)
            except ConflictError:
                return None

    results = await asyncio.gather(*(save(command) for command in commands))
    assert sum(result is not None for result in results) == 1
    index = next(
        index for index, result in enumerate(results) if result is not None
    )
    changed = commands[index].model_copy(update={"device_id": uuid4()})
    async with sessions() as db:
        with pytest.raises(ConflictError):
            await AgentDeviceScriptBindingService(db, owner, owner).save(
                project, actor, employee, changed
            )
        assert (
            await db.scalar(
                select(func.count()).select_from(
                    AgentDeviceScriptBindingRequest
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "case", ["project", "device", "write", "pending", "permission_lost"]
)
async def test_untrusted_or_revoked_owner_evidence_rolls_back(
    database, owner, case
):
    sessions, project, actor, employee, device, _, _ = database
    original = owner.validate_script_configuration

    async def changed(project_id, device_id):
        bound, item = await original(project_id, device_id)
        if case in {"project", "device"}:
            setattr(bound, case + "_id", uuid4())
        elif case == "write":
            item.effect = "write"
        elif case == "pending":
            bound.status = "pending"
        else:
            owner.denied = True
        return bound, item

    owner.validate_script_configuration = changed
    async with sessions() as db:
        with pytest.raises(ExternalServiceError):
            await AgentDeviceScriptBindingService(db, owner, owner).save(
                project,
                actor,
                employee,
                write_command(
                    request_id=uuid4(),
                    expected_revision=0,
                    device_id=device,
                ),
            )
        assert not await db.scalar(select(AgentDeviceScriptBinding.id))
        assert not await db.scalar(select(AgentDeviceScriptBindingRequest.id))


async def test_file_binding_cannot_be_added_while_script_configured(
    database, owner, monkeypatch
):
    from unittest.mock import AsyncMock
    from app.services.agent_service import AgentService
    from app.schemas.agent import AgentUpdate
    from app.services.device_control_client import device_control_client

    sessions, project, actor, employee, device, _, _ = database
    async with sessions() as db:
        await AgentDeviceScriptBindingService(db, owner, owner).save(
            project,
            actor,
            employee,
            write_command(
                request_id=uuid4(),
                expected_revision=0,
                device_id=device,
            ),
        )
        check = AsyncMock(return_value=None)
        monkeypatch.setattr(device_control_client, "validate_binding", check)
        with pytest.raises(ConflictError, match="脚本"):
            await AgentService(db).update_agent(
                project,
                employee,
                AgentUpdate(bound_device_id=str(uuid4())),
                requested_by=actor,
                enrich_resources=False,
            )
        check.assert_not_awaited()


async def test_changed_device_policy_is_not_silently_selected(database, owner):
    sessions, project, actor, employee, device, _, _ = database
    original = owner.validate_script_configuration

    async def changed(project_id, device_id):
        bound, item = await original(project_id, device_id)
        bound.policy.revision = 2
        bound.policy.digest = script_policy_digest(bound.policy)
        return bound, item

    owner.validate_script_configuration = changed
    async with sessions() as db:
        with pytest.raises(ConflictError):
            await AgentDeviceScriptBindingService(db, owner, owner).save(
                project,
                actor,
                employee,
                write_command(
                    request_id=uuid4(), expected_revision=0, device_id=device
                ),
            )
        assert not await db.scalar(select(AgentDeviceScriptBinding.id))
        assert not await db.scalar(select(AgentDeviceScriptBindingRequest.id))
