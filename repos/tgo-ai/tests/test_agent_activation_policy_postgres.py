"""Independent PostgreSQL transactions and real migration/rollback checks."""

import asyncio
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import ConflictError
from app.models import Agent, AgentActivationPolicy, AgentTestReport, BaseModel, Project
from app.schemas.agent_activation import AgentActivationPolicyChange, AgentActivationRequirements, AgentActivationSelection
from app.schemas.agent_drafts import AgentDraftRequest
from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_activation_policies import AgentActivationPolicyService
from app.services.agent_drafts import AgentDraftService
from tests.test_agent_activation_requirements_client import fixture as requirements_fixture

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL database is required")


def owner():
    async def current(query):
        _, body = requirements_fixture()
        body.update(query.model_dump(mode="json"))
        for case in body["knowledge_cases"]:
            case.update(template_id=query.template_id, template_version=query.template_version)
        value = AgentActivationRequirements.model_construct(**query.model_dump(),
            required_resources=body["required_resources"], required_capabilities=body["required_capabilities"],
            knowledge_cases=[KnowledgeReviewCase.model_validate(case) for case in body["knowledge_cases"]])
        value.requirements_revision = value.definition_revision()
        return AgentActivationRequirements.model_validate(value.model_dump())
    return AsyncMock(get_agent_activation_requirements=AsyncMock(side_effect=current),
                     get_agent_configuration_actor=AsyncMock(side_effect=lambda query: AgentConfigurationActor(
                         **query.model_dump(), checked_at=datetime.now(timezone.utc))))


@pytest.mark.asyncio
async def test_policy_migration_concurrency_draft_atomicity_and_legacy_preservation(monkeypatch):
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    schema = "activation_policy_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    engine = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    migration_path = Path(__file__).parents[1] / "migrations/versions/v4w5x6y7z8a9_agent_activation_policies.py"
    spec = importlib.util.spec_from_file_location("activation_policy_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    created = False
    project, actor, target, serving, report = [uuid4() for _ in range(5)]
    authority = owner()
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with engine.begin() as connection:
            def upgrade(connection):
                BaseModel.metadata.create_all(connection, tables=[
                    table for table in BaseModel.metadata.sorted_tables if table is not AgentActivationPolicy.__table__
                ])
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            await connection.run_sync(upgrade)
        async with sessions() as db:
            db.add(Project(id=project, name="隔离策略测试", api_key="synthetic_" + uuid4().hex))
            db.add_all([
                Agent(id=target, project_id=project, name="待测试", model="test", is_active=False, is_default=False),
                Agent(id=serving, project_id=project, name="正在接待", model="test", is_active=True, is_default=True),
                AgentTestReport(id=report, project_id=project, created_by=actor, agent_id=target,
                    request_key=uuid4(), request_digest="a" * 64, status="completed", channel="web",
                    question="历史测试", answer="历史回答", configuration_fingerprint=None,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
            ])
            await db.commit()
        selection = AgentActivationSelection(template_id="knowledge", template_version=1, channel="web")
        async def save(revision=0, selected=selection):
            async with sessions() as db:
                return await AgentActivationPolicyService(db, authority).save(project, actor, target,
                    AgentActivationPolicyChange(expected_revision=revision, selection=selected))
        receipts = await asyncio.wait_for(asyncio.gather(save(), save()), 20)
        assert receipts[0] == receipts[1] and receipts[0].revision == 1
        results = await asyncio.wait_for(asyncio.gather(
            save(1, selection.model_copy(update={"template_id": "aftersales"})),
            save(1, selection.model_copy(update={"template_version": 2})), return_exceptions=True,
        ), 20)
        assert sum(isinstance(result, ConflictError) for result in results) == 1
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(AgentActivationPolicy)) == 1
            policy = await db.get(AgentActivationPolicy, receipts[0].id)
            assert policy.revision == 2
            with pytest.raises(IntegrityError):
                await db.execute(update(AgentActivationPolicy).where(AgentActivationPolicy.id == policy.id)
                    .values(source_draft_id=uuid4(), source_draft_revision=None))
            await db.rollback()

        current_requirements = authority.get_agent_activation_requirements.side_effect
        authority.get_agent_activation_requirements.side_effect = ConflictError("旧模板已下线")
        async with sessions() as db:
            row = await db.get(AgentActivationPolicy, receipts[0].id)
            row.template_version = 999
            await db.commit()
        async def retire():
            async with sessions() as db:
                return await AgentActivationPolicyService(db, authority).remove(project, actor, target, selection.channel, 2)
        removals = await asyncio.wait_for(asyncio.gather(retire(), retire()), 20)
        assert removals[0] == removals[1] and not removals[0].enabled and removals[0].revision == 3
        assert authority.get_agent_configuration_actor.await_count == 2
        authority.get_agent_activation_requirements.side_effect = current_requirements

        request = AgentDraftRequest.model_validate({"revision": 1,
            "activation_selection": selection.model_dump(mode="json"), "agent": {
                "name": "澜月测试客服", "model": "__system_default__", "instruction": "按审核资料回答",
                "is_active": False, "is_default": False,
            }})
        draft = uuid4()
        async def save_draft(payload=request, identifier=draft, failure=False):
            async with sessions() as db:
                service = AgentDraftService(db)
                service.policies.owner = authority
                if failure:
                    monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("synthetic rollback")))
                return await service.save(project, actor, identifier, payload)
        drafts = await asyncio.wait_for(asyncio.gather(save_draft(), save_draft()), 20)
        assert drafts[0] == drafts[1] and drafts[0].activation_selection == selection
        assert not drafts[0].is_active and not drafts[0].can_enable
        changed = request.model_copy(deep=True)
        changed.revision = 2
        changed.activation_selection.channel = selection.channel.__class__.WECOM_KF
        updated = await save_draft(changed)
        assert updated.agent_id == drafts[0].agent_id and updated.activation_selection.channel.value == "wecom_kf"
        omitted = changed.model_copy(update={"activation_selection": None, "revision": 3})
        with pytest.raises(ConflictError):
            await save_draft(omitted)
        with pytest.raises(RuntimeError):
            await save_draft(identifier=uuid4(), failure=True)
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(Agent)) == 3
            assert (await db.get(Agent, serving)).is_active is True
            assert (await db.get(AgentTestReport, report)).answer == "历史回答"
            policies = await AgentActivationPolicyService(db, authority).list(project, updated.agent_id)
            assert len(policies.policies) == 2
            assert [value.channel.value for value in policies.policies if value.enabled] == ["wecom_kf"]
            row = await db.get(AgentActivationPolicy, next(value.id for value in policies.policies if value.enabled))
            assert row.source_draft_revision == 2
        async with engine.begin() as connection:
            def downgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            await connection.run_sync(downgrade)
            assert (await connection.execute(text("SELECT answer FROM ai_agent_test_reports WHERE id=:id"), {"id": report})).scalar_one() == "历史回答"
            assert (await connection.execute(text("SELECT is_active FROM ai_agents WHERE id=:id"), {"id": serving})).scalar_one() is True
            def reupgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            await connection.run_sync(reupgrade)
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
