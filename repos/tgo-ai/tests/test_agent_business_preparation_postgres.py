"""Owned PostgreSQL proves preparation cancellation, locks, and migration recovery."""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import ConflictError
from app.models import Agent, BaseModel
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_request import AgentBusinessRequest
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_connections import BusinessConnectionOwner
from app.schemas.agent_business_requests import BusinessRequestControl
from app.schemas.agent_business_trials import BusinessTrialRequest
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_activation import lock_account_agents
from app.services.agent_business_preparation import AgentBusinessPreparation
from app.services.agent_business_trials import AgentBusinessTrialService

URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="Explicit owned isolated PostgreSQL required"
)


@pytest.mark.asyncio
async def test_durable_preparation_migration_cancel_and_sql_lock_wait():
    assert URL
    url = make_url(URL)
    assert url.host in {"localhost", "127.0.0.1"} and url.port == 15432
    assert (url.database or "").startswith("tgo_scenarios_test_")
    schema = "preparation_" + uuid4().hex
    admin = create_async_engine(URL)
    engine = create_async_engine(
        URL, connect_args={"server_settings": {"search_path": schema}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/b0c1d2e3f4a5_business_preparation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "business_preparation_migration", path
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection, method):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, method)()

    project, actor, agent, connection = [uuid4() for _ in range(4)]
    proof, proof_until = uuid4(), datetime.now(UTC) + timedelta(hours=1)
    owner = AsyncMock()
    owner.get_agent_configuration_actor.side_effect = lambda query: (
        AgentConfigurationActor(**query.model_dump(), checked_at=datetime.now(UTC))
    )

    def metadata(query):
        return BusinessConnectionOwner(
            **query.model_dump(),
            name="隔离商户",
            mode="http",
            enabled=True,
            operations=["order_query"],
            revision=1,
            configuration_revision=1,
            authorization_revision=1,
            resource_revision="a" * 64,
            test_status="passed",
            checked_at=datetime.now(UTC),
            configuration_tests=[
                {
                    "operation": "order_query",
                    "report_id": proof,
                    "status": "current",
                    "valid_until": proof_until,
                    "issues": [],
                }
            ],
        )

    owner.get_agent_business_connection.side_effect = metadata
    fingerprint = AsyncMock(return_value="b" * 64)
    request = BusinessTrialRequest(
        request_id=uuid4(),
        connection_id=connection,
        operation="order_query",
        visitor_id=uuid4(),
        order_no=SecretStr("PRIVATE-ORDER-123"),
        customer_session=SecretStr("private-session"),
    )
    control = BusinessRequestControl.model_validate(
        request.model_dump(include=set(BusinessRequestControl.model_fields))
    )

    def trials(db):
        return AgentBusinessTrialService(db, owner=owner, fingerprint=fingerprint)

    try:
        async with admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            await db.run_sync(
                lambda sync: BaseModel.metadata.create_all(
                    sync,
                    tables=[
                        table
                        for name, table in BaseModel.metadata.tables.items()
                        if name != AgentBusinessRequest.__tablename__
                    ],
                )
            )
            for method in ("upgrade", "downgrade", "upgrade"):
                await db.run_sync(migrate, method)
            columns = await db.run_sync(
                lambda sync: inspect(sync).get_columns(
                    AgentBusinessRequest.__tablename__
                )
            )
            assert {column["name"] for column in columns} == set(
                AgentBusinessRequest.__table__.columns.keys()
            )
            assert all(
                column["nullable"]
                == AgentBusinessRequest.__table__.columns[column["name"]].nullable
                for column in columns
            )
        async with sessions() as db:
            db.add(
                Agent(
                    id=agent,
                    project_id=project,
                    name="隔离测试员工",
                    model="fixture",
                    is_active=False,
                )
            )
            await db.flush()
            db.add(
                AgentBusinessConnection(
                    project_id=project,
                    agent_id=agent,
                    connection_id=connection,
                    operations=["order_query"],
                    enabled=True,
                    revision=1,
                    configuration_revision=1,
                    authorization_revision=1,
                    resource_revision="a" * 64,
                    configured_by=actor,
                )
            )
            await db.commit()
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(query):
            entered.set()
            await release.wait()
            return metadata(query)

        owner.get_agent_business_connection.side_effect = delayed

        async def prepare():
            async with sessions() as db:
                return await trials(db).prepare(project, actor, agent, request)

        started = asyncio.create_task(prepare())
        await asyncio.wait_for(entered.wait(), 3)
        async with sessions() as db:
            lifecycle = AgentBusinessPreparation(trials(db))
            state = await lifecycle.state(project, actor, agent, request.request_id)
            assert state.status == "preparing"
            with pytest.raises(ConflictError):
                await trials(db).prepare(project, actor, agent, request)
            cancelled = await lifecycle.cancel(project, actor, agent, control)
            assert cancelled.status == "cancelled"
        release.set()
        with pytest.raises(ConflictError):
            await started
        async with sessions() as db:
            assert await db.scalar(select(AgentBusinessTrial.id)) is None
            assert (
                await AgentBusinessPreparation(trials(db)).state(
                    project, actor, agent, request.request_id
                )
            ).status == "cancelled"
        owner.get_agent_business_connection.side_effect = metadata
        # SQL advisory lock contention is bounded; no blocked request can outlive
        # a successful original-key cancellation after that transaction releases.
        locked_request = request.model_copy(update={"request_id": uuid4()})
        async with sessions() as blocker:
            await lock_account_agents(blocker, project)
            async with sessions() as db:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(
                        AgentBusinessPreparation(trials(db), lock_seconds=0.1).prepare(
                            project, actor, agent, locked_request
                        ),
                        2,
                    )
            await blocker.rollback()
        async with sessions() as db:
            blocked_control = control.model_copy(
                update={"request_id": locked_request.request_id}
            )
            cancelled = await AgentBusinessPreparation(trials(db)).cancel(
                project, actor, agent, blocked_control
            )
            assert cancelled.status == "cancelled"
            with pytest.raises(ConflictError):
                await trials(db).prepare(project, actor, agent, locked_request)
        owner.query_agent_business_trial.assert_not_awaited()
        with pytest.raises(RuntimeError, match="receipts exist"):
            async with engine.begin() as db:
                await db.run_sync(migrate, "downgrade")
        # Replay the migration against a legacy report with the new ledger empty.
        async with sessions() as db:
            await db.execute(text("DELETE FROM ai_agent_business_requests"))
            await db.commit()
            saved, command = await trials(db).prepare(
                project,
                actor,
                agent,
                request.model_copy(update={"request_id": uuid4()}),
            )
            await db.execute(text("DELETE FROM ai_agent_business_requests"))
            await db.commit()
        async with engine.begin() as db:
            await db.run_sync(migrate, "downgrade")
            await db.run_sync(migrate, "upgrade")
        async with sessions() as db:
            recovered = await AgentBusinessPreparation(trials(db)).state(
                project, actor, agent, command.request_id
            )
            assert recovered.status == "ready" and recovered.report.id == saved.id
            assert "PRIVATE-ORDER-123" not in recovered.model_dump_json()
            assert "private-session" not in recovered.model_dump_json()
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
