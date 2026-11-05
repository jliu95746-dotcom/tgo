"""Recover the saved employee without taking ownership of independent policies."""
import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import ConflictError, NotFoundError
from app.models import Agent, AgentActivationPolicy, BaseModel, Project
from app.models.agent_draft_binding import AgentDraftBinding
from app.schemas.agent_activation import AgentActivationPolicyChange
from app.schemas.agent_drafts import AgentDraftRequest
from app.services.agent_drafts import AgentDraftService
from tests.test_agent_activation_policy_postgres import owner


@pytest_asyncio.fixture
async def saved():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, draft = uuid4(), uuid4(), uuid4()
        service = AgentDraftService(db)
        service.policies.owner = owner()
        request = AgentDraftRequest.model_validate(
            {
                "revision": 1,
                "activation_selection": {
                    "template_id": "knowledge",
                    "template_version": 1,
                    "channel": "web",
                },
                "agent": {
                    "name": "恢复测试客服",
                    "model": "__system_default__",
                    "instruction": "按资料回答",
                    "is_active": False,
                    "is_default": False,
                    "config": {"profession": "资料咨询"},
                },
            }
        )
        receipt = await service.save(project, actor, draft, request)
        yield db, service, project, actor, draft, request, receipt
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["same_selection", "different_role", "removed"])
async def test_resume_preserves_independent_policy_and_original_employee(saved, edit):
    db, service, project, actor, draft, request, receipt = saved
    policies = service.policies
    first = (await policies.list(project, receipt.agent_id)).policies[0]
    if edit == "removed":
        await policies.remove(
            project, actor, receipt.agent_id, first.channel, first.revision
        )
    else:
        selection = request.activation_selection.model_copy(deep=True)
        if edit == "different_role":
            selection.template_id = "aftersales"
        await policies.save(
            project,
            actor,
            receipt.agent_id,
            AgentActivationPolicyChange(
                expected_revision=first.revision, selection=selection
            ),
        )
    before = await policies.list(project, receipt.agent_id)
    # Reading/retrying the old employee snapshot must not re-run requirements or restore a policy.
    policies.owner.get_agent_activation_requirements.side_effect = AssertionError(
        "Unexpected policy write"
    )
    recovered = await service.get(project, actor, draft)
    assert (
        recovered.agent_id == receipt.agent_id
        and recovered.revision == receipt.revision
    )
    assert recovered.activation_selection is None
    assert recovered.is_active is False and recovered.can_enable is False
    repeated = await service.save(project, actor, draft, request)
    assert (
        repeated.agent_id == receipt.agent_id and repeated.revision == receipt.revision
    )
    # This is the original, digest-checked request, not the live activation policy.
    assert repeated.activation_selection == request.activation_selection
    assert repeated.is_active is False and repeated.can_enable is False
    assert await policies.list(project, receipt.agent_id) == before
    assert await db.scalar(select(func.count()).select_from(Agent)) == 1
    row = await db.scalar(select(AgentActivationPolicy))
    assert row.revision == before.policies[0].revision


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("AI_POSTGRES_TEST_URL"),
    reason="An isolated PostgreSQL test database is required",
)
async def test_postgres_fresh_sessions_resume_without_rewriting_independent_policy():
    url = os.environ["AI_POSTGRES_TEST_URL"]
    assert (make_url(url).database or "").startswith("tgo_scenarios_test_")
    schema = "draft_resume_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    created = False
    project, actor, editor, draft = [uuid4() for _ in range(4)]
    payload = AgentDraftRequest.model_validate(
        {
            "revision": 1,
            "activation_selection": {
                "template_id": "knowledge",
                "template_version": 1,
                "channel": "web",
            },
            "agent": {
                "name": "独立事务恢复测试",
                "model": "__system_default__",
                "instruction": "按资料回答",
                "is_active": False,
                "is_default": False,
            },
        }
    )

    async def save():
        async with sessions() as db:
            service = AgentDraftService(db)
            service.policies.owner = owner()
            return await service.save(project, actor, draft, payload)

    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)
        async with sessions() as db:
            db.add(
                Project(id=project, name="隔离恢复测试", api_key="synthetic_" + uuid4().hex)
            )
            await db.commit()
        original = await save()
        async with sessions() as db:
            service = AgentDraftService(db)
            service.policies.owner = owner()
            selection = payload.activation_selection.model_copy(
                update={"template_id": "aftersales"}
            )
            changed = await service.policies.save(
                project,
                editor,
                original.agent_id,
                AgentActivationPolicyChange(expected_revision=1, selection=selection),
            )
        recovered = await asyncio.wait_for(asyncio.gather(save(), save()), 20)
        assert all(
            row.agent_id == original.agent_id
            and row.revision == 1
            and row.activation_selection == payload.activation_selection
            and row.is_active is False
            and row.can_enable is False
            for row in recovered
        )
        async with sessions() as db:
            service = AgentDraftService(db)
            service.policies.owner = owner()
            receipt = await service.get(project, actor, draft)
            assert (
                receipt.agent_id == original.agent_id
                and receipt.activation_selection is None
            )
            fresh = payload.model_copy(deep=True)
            fresh.revision = 2
            fresh.agent.name = "不可覆盖新岗位"
            with pytest.raises(ConflictError):
                await service.save(project, actor, draft, fresh)
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(Agent)) == 1
            assert (
                await db.scalar(select(func.count()).select_from(AgentDraftBinding))
                == 1
            )
            assert (await db.get(Agent, original.agent_id)).name == payload.agent.name
            policy = await db.scalar(select(AgentActivationPolicy))
            assert policy.revision == changed.revision == 2
            assert policy.template_id == "aftersales" and policy.updated_by == editor
            assert policy.source_draft_id is None and policy.deleted_at is None
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_resuming_cannot_change_original_selection_or_overwrite_independent_policy(
    saved,
):
    db, service, project, actor, draft, request, receipt = saved
    first = (await service.policies.list(project, receipt.agent_id)).policies[0]
    await service.policies.save(
        project,
        actor,
        receipt.agent_id,
        AgentActivationPolicyChange(
            expected_revision=first.revision, selection=request.activation_selection
        ),
    )
    before = await service.policies.list(project, receipt.agent_id)
    collision = request.model_copy(deep=True)
    collision.activation_selection.template_id = "aftersales"
    with pytest.raises(ConflictError):
        await service.save(project, actor, draft, collision)
    changed = request.model_copy(deep=True)
    changed.revision = 2
    changed.agent.name = "不可覆盖"
    with pytest.raises(ConflictError):
        await service.save(project, actor, draft, changed)
    assert (
        await db.get(Agent, receipt.agent_id, populate_existing=True)
    ).name == request.agent.name
    assert await service.policies.list(project, receipt.agent_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["instruction", "config", "skills_enabled"])
async def test_actual_employee_edits_still_reject_old_snapshot(saved, field):
    db, service, project, actor, draft, request, receipt = saved
    agent = await db.get(Agent, receipt.agent_id)
    replacement = {
        "instruction": "其他页面的新职责",
        "config": {"profession": "已修改"},
        "skills_enabled": not request.agent.skills_enabled,
    }[field]
    setattr(agent, field, replacement)
    await db.commit()
    with pytest.raises(ConflictError):
        await service.save(project, actor, draft, request)
    assert (
        getattr(await db.get(Agent, receipt.agent_id, populate_existing=True), field)
        == replacement
    )
    assert await db.scalar(select(func.count()).select_from(Agent)) == 1


@pytest.mark.asyncio
async def test_resume_keeps_owner_deleted_and_active_boundaries(saved):
    db, service, project, actor, draft, request, receipt = saved
    for other_project, other_actor in [(uuid4(), actor), (project, uuid4())]:
        with pytest.raises(NotFoundError):
            await service.get(other_project, other_actor, draft)
    with pytest.raises(NotFoundError):
        await service.save(project, uuid4(), draft, request)
    agent = await db.get(Agent, receipt.agent_id)
    agent.is_active = True
    await db.commit()
    assert (await service.save(project, actor, draft, request)).is_active is True
    assert (await db.get(Agent, receipt.agent_id)).is_active is True
    agent.soft_delete()
    await db.commit()
    with pytest.raises(ConflictError):
        await service.get(project, actor, draft)
    with pytest.raises(ConflictError):
        await service.save(project, actor, draft, request)
    assert await db.scalar(select(func.count()).select_from(Agent)) == 1
