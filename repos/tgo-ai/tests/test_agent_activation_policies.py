"""Durable channel policies never activate or retire customer-facing employees."""

from unittest.mock import AsyncMock
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import AuthorizationError, ConflictError, ExternalServiceError, NotFoundError
from app.models import Agent, AgentActivationPolicy, BaseModel
from app.schemas.agent_activation import AgentActivationPolicyChange, AgentActivationSelection, AgentActivationRequirements
from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_activation_policies import AgentActivationPolicyService
from tests.test_agent_activation_requirements_client import fixture as requirements_fixture


@pytest_asyncio.fixture
async def setup(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'policies.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, agent, serving = [uuid4() for _ in range(4)]
        db.add_all([
            Agent(id=agent, project_id=project, name="待启用员工", model="test", is_active=False, is_default=False),
            Agent(id=serving, project_id=project, name="正在接待", model="test", is_active=True, is_default=True),
        ])
        await db.commit()
        owner = AsyncMock()
        owner.get_agent_configuration_actor.side_effect = lambda query: AgentConfigurationActor(**query.model_dump(), checked_at=datetime.now(UTC))

        async def current(query):
            _, body = requirements_fixture()
            body.update(query.model_dump(mode="json"))
            for case in body["knowledge_cases"]:
                case.update(template_id=query.template_id, template_version=query.template_version)
            value = AgentActivationRequirements.model_construct(**query.model_dump(),
                required_resources=body["required_resources"], required_capabilities=body["required_capabilities"],
                knowledge_cases=[KnowledgeReviewCase.model_validate(row) for row in body["knowledge_cases"]])
            value.requirements_revision = value.definition_revision()
            return AgentActivationRequirements.model_validate(value.model_dump())

        owner.get_agent_activation_requirements.side_effect = current
        service = AgentActivationPolicyService(db, owner)
        yield db, service, owner, project, actor, agent, serving
    await engine.dispose()


def change(revision=0, channel="web", template="knowledge"):
    return AgentActivationPolicyChange(expected_revision=revision, selection=AgentActivationSelection(
        template_id=template, template_version=1, channel=channel))


@pytest.mark.asyncio
async def test_removed_template_can_be_retired_using_current_actor_permissions(setup):
    _, service, owner, project, actor, agent, _ = setup
    record = await service.save(project, actor, agent, change())
    owner.get_agent_activation_requirements.side_effect = ConflictError("岗位模板已下线")
    removed = await service.remove(project, actor, agent, record.channel, record.revision)
    assert removed.enabled is False and removed.revision == record.revision + 1
    retry = await service.remove(project, actor, agent, record.channel, record.revision)
    assert retry.id == removed.id and retry.revision == removed.revision
    assert owner.get_agent_configuration_actor.call_args.args[0].requested_by == actor


@pytest.mark.asyncio
async def test_retiring_old_template_still_requires_current_actor_permissions(setup):
    _, service, owner, project, actor, agent, _ = setup
    record = await service.save(project, actor, agent, change())
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("当前管理员权限已撤销")
    with pytest.raises(AuthorizationError):
        await service.remove(project, actor, agent, record.channel, record.revision)
    current = (await service.list(project, agent)).policies[0]
    assert current.enabled is True and current.revision == record.revision


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", ["project_id", "requested_by"])
async def test_permission_receipt_from_another_scope_cannot_remove_policy(setup, foreign):
    _, service, owner, project, actor, agent, _ = setup
    record = await service.save(project, actor, agent, change())
    owner.get_agent_configuration_actor.side_effect = lambda query: AgentConfigurationActor(**{
        **query.model_dump(), foreign: uuid4(), "checked_at": datetime.now(UTC)})
    with pytest.raises(ExternalServiceError):
        await service.remove(project, actor, agent, record.channel, record.revision)
    assert (await service.list(project, agent)).policies[0].enabled is True


@pytest.mark.asyncio
async def test_save_retry_read_and_owner_identity(setup):
    db, service, owner, project, actor, agent, serving = setup
    first = await service.save(project, actor, agent, change())
    repeated = await service.save(project, actor, agent, change())
    assert first.id == repeated.id and first.revision == repeated.revision == 1
    assert first.template_id == "knowledge" and first.channel.value == "web"
    assert first.configured_by == actor and first.enabled
    assert len((await db.scalars(select(AgentActivationPolicy))).all()) == 1
    listed = await service.list(project, agent)
    assert listed.agent_id == agent and listed.can_enable is False and len(listed.policies) == 1
    assert owner.get_agent_activation_requirements.call_args.args[0].project_id == project
    assert owner.get_agent_activation_requirements.call_args.args[0].requested_by == actor
    assert (await db.get(Agent, agent)).is_active is False
    assert (await db.get(Agent, serving)).is_active is True


@pytest.mark.asyncio
async def test_another_admin_saving_current_selection_gets_their_own_receipt(setup):
    _, service, _, project, actor, agent, _ = setup
    first = await service.save(project, actor, agent, change())
    second_actor = uuid4()
    second = await service.save(project, second_actor, agent, change(first.revision))
    assert second.configured_by == second_actor and second.revision == first.revision + 1
    retry = await service.save(project, second_actor, agent, change(first.revision))
    assert retry.id == second.id and retry.revision == second.revision
    with pytest.raises(ConflictError):
        await service.save(project, actor, agent, change(first.revision))


@pytest.mark.asyncio
async def test_conflicting_retries_and_stale_edit_leave_policy_unchanged(setup):
    _, service, _, project, actor, agent, _ = setup
    first = await service.save(project, actor, agent, change())
    with pytest.raises(ConflictError):
        await service.save(project, actor, agent, change(template="aftersales"))
    updated = await service.save(project, actor, agent, change(1, template="aftersales"))
    assert updated.id == first.id and updated.revision == 2
    with pytest.raises(ConflictError):
        await service.save(project, actor, agent, change(0))
    assert (await service.list(project, agent)).policies[0].template_id == "aftersales"


@pytest.mark.asyncio
async def test_channel_policies_are_separate_and_removal_retains_revision(setup):
    _, service, _, project, actor, agent, _ = setup
    web = await service.save(project, actor, agent, change())
    wecom = await service.save(project, actor, agent, change(channel="wecom_kf"))
    assert web.id != wecom.id
    removed = await service.remove(project, actor, agent, web.channel, 1)
    assert removed.id == web.id and removed.revision == 2 and not removed.enabled
    assert (await service.list(project, agent)).policies[1].enabled
    with pytest.raises(ConflictError):
        await service.save(project, actor, agent, change())
    restored = await service.save(project, actor, agent, change(2))
    assert restored.id == web.id and restored.revision == 3 and restored.enabled


@pytest.mark.asyncio
async def test_foreign_deleted_or_unknown_agent_never_contacts_owner(setup):
    db, service, owner, project, actor, agent, _ = setup
    for p, identifier in [(uuid4(), agent), (project, uuid4())]:
        with pytest.raises(NotFoundError):
            await service.save(p, actor, identifier, change())
    row = await db.get(Agent, agent)
    row.soft_delete()
    await db.commit()
    with pytest.raises(NotFoundError):
        await service.save(project, actor, agent, change())
    owner.get_agent_activation_requirements.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_role_cannot_replace_or_remove_a_saved_policy(setup):
    _, service, owner, project, actor, agent, _ = setup
    first = await service.save(project, actor, agent, change())
    owner.get_agent_activation_requirements.side_effect = AuthorizationError("当前账号没有权限")
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("当前账号没有权限")
    with pytest.raises(AuthorizationError):
        await service.save(project, actor, agent, change(1, template="aftersales"))
    with pytest.raises(AuthorizationError):
        await service.remove(project, actor, agent, first.channel, 1)
    policies = (await service.list(project, agent)).policies
    assert policies[0].revision == 1 and policies[0].enabled


@pytest.mark.asyncio
async def test_active_employee_policy_is_readonly_until_explicit_stop(setup):
    db, service, _, project, actor, agent, serving = setup
    first = await service.save(project, actor, agent, change())
    await db.execute(update(Agent).where(Agent.id == serving).values(is_active=False, is_default=False))
    await db.flush()
    row = await db.get(Agent, agent)
    row.is_active = True
    await db.commit()
    assert (await service.save(project, actor, agent, change(1))).id == first.id
    for operation in [service.save(project, actor, agent, change(1, template="aftersales")),
                      service.save(project, actor, agent, change(channel="wecom_kf")),
                      service.remove(project, actor, agent, first.channel, 1)]:
        with pytest.raises(ConflictError):
            await operation
    assert (await db.get(Agent, agent)).is_active is True


@pytest.mark.asyncio
async def test_draft_policy_is_atomic_and_missing_old_field_cannot_remove_it(setup):
    db, service, _, project, actor, agent, _ = setup
    draft = uuid4()
    selection = change().selection
    await service.save_for_draft(project, actor, agent, draft, 1, selection)
    await db.commit()
    assert await service.draft_selection(project, agent, draft, 1) == selection
    with pytest.raises(ConflictError):
        await service.save_for_draft(project, actor, agent, draft, 2, None)
    assert len((await service.list(project, agent)).policies) == 1
    await service.save_for_draft(project, actor, agent, draft, 2,
                                 selection.model_copy(update={"channel": selection.channel.__class__.WECOM_KF}))
    await db.rollback()
    assert await service.draft_selection(project, agent, draft, 1) == selection


@pytest.mark.asyncio
async def test_draft_same_revision_cannot_change_channel_or_restore_removed_policy(setup):
    _, service, _, project, actor, agent, _ = setup
    draft, selection = uuid4(), change().selection
    await service.save_for_draft(project, actor, agent, draft, 1, selection)
    await service.db.commit()
    with pytest.raises(ConflictError):
        await service.save_for_draft(project, actor, agent, draft, 1, change(channel="wecom_kf").selection)
    record = (await service.list(project, agent)).policies[0]
    await service.remove(project, actor, agent, record.channel, record.revision)
    with pytest.raises(ConflictError):
        await service.save_for_draft(project, actor, agent, draft, 1, selection)
