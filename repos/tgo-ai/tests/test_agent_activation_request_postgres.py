"""Concurrent unknown-result retry and receipt migration on an owned PostgreSQL schema."""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, AgentActivationRequestRecord, BaseModel, Project
from app.schemas.agent_activation_requests import AgentActivationRequest
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.agent_activation_requests import AgentActivationRequestService
from tests.test_account_agent_activation import proof

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="An isolated PostgreSQL database is required"
)


@pytest.mark.asyncio
async def test_migration_and_uncommitted_original_retry_preserve_one_atomic_switch(
    monkeypatch,
):
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith(
        "tgo_scenarios_test_"
    )
    schema = "activation_request_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    engine = create_async_engine(
        TEST_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/x6y7z8a9b0c1_activation_request_receipts.py"
    )
    spec = importlib.util.spec_from_file_location("activation_request_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    project, actor, old_id, target = [uuid4() for _ in range(4)]
    owner = AsyncMock()
    owner.get_agent_configuration_actor.side_effect = lambda query: (
        AgentConfigurationActor(**query.model_dump(), checked_at=datetime.now(UTC))
    )
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)
            await connection.execute(text("DROP TABLE ai_agent_activation_requests"))

            def exercise(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                    migration.downgrade()
                    migration.upgrade()

            await connection.run_sync(exercise)
        async with sessions() as db:
            db.add(
                Project(id=project, name="回执并发", api_key="synthetic_" + uuid4().hex)
            )
            db.add_all(
                [
                    Agent(
                        id=old_id,
                        project_id=project,
                        name="原接待",
                        model="fixture",
                        is_active=True,
                        is_default=True,
                        skills_enabled=False,
                    ),
                    Agent(
                        id=target,
                        project_id=project,
                        name="待启用",
                        model="fixture",
                        is_active=False,
                        is_default=False,
                        skills_enabled=False,
                    ),
                ]
            )
            await db.commit()
        evidence = proof(project, actor, target)
        command = AgentActivationRequest.model_validate(
            {
                "request_key": uuid4(),
                "expected": {
                    "checked_at": evidence.checked_at,
                    "configuration_fingerprint": evidence.configuration_fingerprint,
                    "model_owner_revision": evidence.model_owner_revision,
                    "channels": [
                        row.model_dump(exclude={"status", "valid_until", "business_capabilities"})
                        for row in evidence.channels
                    ],
                },
            }
        )
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def read(*args):
            calls.append(args)
            started.set()
            await release.wait()
            return evidence

        monkeypatch.setattr(AgentActivationReadinessService, "read", read)

        async def submit():
            async with sessions() as db:
                return await AgentActivationRequestService(db, owner).submit(
                    project, actor, target, command
                )

        first = asyncio.create_task(submit())
        await asyncio.wait_for(started.wait(), 10)
        async with sessions() as db:
            missing = await AgentActivationRequestService(db, owner).lookup(
                project, actor, target, command.request_key
            )
            assert missing.status == "not_found"
            assert (await db.get(Agent, old_id)).is_active is True
        retry = asyncio.create_task(submit())
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, retry), 25)
        assert (
            results[0] == results[1]
            and results[0].status == "completed"
            and len(calls) == 1
        )
        async with sessions() as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(AgentActivationRequestRecord)
                )
                == 1
            )
            assert (await db.get(Agent, old_id)).is_active is False and (
                await db.get(Agent, target)
            ).is_active is True
            # Another later operator stops the employee; historical replay must not enable it again.
            await db.execute(
                update(Agent)
                .where(Agent.id == target)
                .values(is_active=False, is_default=False)
            )
            await db.commit()
        assert (await submit()).status == "completed" and len(calls) == 1
        async with sessions() as db:
            assert (await db.get(Agent, target)).is_active is False
        async with engine.begin() as connection:

            def preserve_history(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(RuntimeError, match="receipts exist"):
                        migration.downgrade()

            await connection.run_sync(preserve_history)
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM ai_agent_activation_requests")
                )
            ).scalar_one() == 1
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
