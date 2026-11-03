"""Real mutation services cannot bypass current evidence or retire peers on rejection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import AuthorizationError, ConflictError, TGOAIServiceException
from app.models import Agent, BaseModel, Project
from app.models.collection import AgentCollection
from app.models.workflow import AgentWorkflow
from app.models.agent import AgentToolAssociation
from app.models.tool import Tool, ToolType
from app.schemas.agent import AgentCreate, AgentUpdate
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.agent_service import AgentService


@pytest_asyncio.fixture
async def fixture(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, old_id, target_id, foreign_id = [uuid4() for _ in range(5)]
        db.add(Project(id=project, name="门禁隔离测试", api_key="synthetic_" + uuid4().hex))
        db.add_all([
            Agent(id=old_id, project_id=project, name="正在接待", model="fixture", instruction="旧职责",
                is_active=True, is_default=True, skills_enabled=False),
            Agent(id=target_id, project_id=project, name="待启用", model="fixture", instruction="已测试职责",
                is_active=False, is_default=False, skills_enabled=False),
            Agent(id=foreign_id, project_id=uuid4(), name="其他企业", model="fixture",
                is_active=True, is_default=True, skills_enabled=False),
        ])
        await db.commit()
        receipt = AgentActivationReadiness(project_id=project, agent_id=target_id, checked_by=actor,
            checked_at=datetime.now(UTC), enabled=False, can_enable=True,
            configuration_fingerprint="a" * 64, model_owner_revision="b" * 64,
            channels=[{"template_id": "knowledge", "template_version": 1, "channel": "web",
                "policy_revision": 1, "requirements_revision": "c" * 64, "status": "passed",
                "valid_until": datetime.now(UTC) + timedelta(hours=1)}])
        gate = AsyncMock(return_value=receipt)
        monkeypatch.setattr(AgentActivationReadinessService, "read", gate)
        service = AgentService(db)
        yield db, service, project, actor, old_id, target_id, foreign_id, gate, receipt
    await engine.dispose()


async def state(db, identifier):
    return await db.scalar(select(Agent).where(Agent.id == identifier).execution_options(populate_existing=True))


@pytest.mark.asyncio
async def test_default_creation_is_paused_and_keeps_the_current_employee(fixture):
    db, service, project, _, old_id, _, _, gate, _ = fixture
    created = await service.create_agent(project, AgentCreate(name="新建草稿", model="fixture"))
    assert not created.is_active and not created.is_default
    assert (await state(db, old_id)).is_active
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_computer_use_category_is_not_dropped_from_the_saved_draft(fixture):
    _, service, project, _, _, _, _, _, _ = fixture
    created = await service.create_agent(project, AgentCreate(name="待配置屏幕员工", model="fixture",
        agent_category="computer_use", skills_enabled=False))
    assert created.agent_category == "computer_use" and not created.is_active


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"is_active": True}, {"is_default": True}])
async def test_new_employee_cannot_be_enabled_before_its_first_saved_test(fixture, flags):
    db, service, project, _, old_id, _, _, _, _ = fixture
    with pytest.raises(TGOAIServiceException):
        await service.create_agent(project, AgentCreate(name="绕过测试的新员工", model="fixture", **flags))
    assert (await state(db, old_id)).is_active
    assert not await db.scalar(select(Agent.id).where(Agent.name == "绕过测试的新员工"))


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"is_active": True}, {"is_default": True}])
async def test_direct_enablement_without_trusted_actor_is_denied(fixture, flags):
    db, service, project, _, old_id, target_id, _, gate, _ = fixture
    with pytest.raises(AuthorizationError):
        await service.update_agent(project, target_id, AgentUpdate(**flags), enrich_resources=False)
    assert (await state(db, old_id)).is_active
    assert not (await state(db, target_id)).is_active
    gate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"is_active": True}, {"is_default": True}])
async def test_verified_enablement_is_an_atomic_project_scoped_switch(fixture, flags):
    db, service, project, actor, old_id, target_id, foreign_id, gate, _ = fixture
    result = await service.update_agent(project, target_id, AgentUpdate(**flags),
        requested_by=actor, enrich_resources=False)
    assert result.is_active and result.is_default
    assert not (await state(db, old_id)).is_active
    assert (await state(db, foreign_id)).is_active
    assert gate.await_args.args == (project, actor, target_id)


@pytest.mark.asyncio
async def test_gate_sees_updated_configuration_before_peers_are_retired_and_rejection_rolls_back(fixture):
    db, service, project, actor, old_id, target_id, _, gate, receipt = fixture
    observed = []

    async def reject(*args):
        observed.append(((await state(db, target_id)).instruction, (await state(db, old_id)).is_active))
        return receipt.model_copy(update={"can_enable": False,
            "blockers": [{"code": "tests_not_current", "message": "职责已修改，请重新测试"}]})

    gate.side_effect = reject
    with pytest.raises(ConflictError):
        await service.update_agent(project, target_id, AgentUpdate(instruction="新职责", is_active=True),
            requested_by=actor, enrich_resources=False)
    assert observed == [("新职责", True)]
    assert (await state(db, target_id)).instruction == "已测试职责"
    assert (await state(db, old_id)).is_active


@pytest.mark.asyncio
async def test_revocation_during_check_never_retires_the_current_employee(fixture):
    db, service, project, actor, old_id, target_id, _, gate, _ = fixture
    gate.side_effect = AuthorizationError("管理员权限已撤销")
    with pytest.raises(AuthorizationError):
        await service.update_agent(project, target_id, AgentUpdate(is_active=True),
            requested_by=actor, enrich_resources=False)
    assert (await state(db, old_id)).is_active and not (await state(db, target_id)).is_active


@pytest.mark.asyncio
async def test_cosmetic_live_edit_and_paused_business_edit_need_no_activation_proof(fixture):
    db, service, project, _, old_id, target_id, _, gate, _ = fixture
    await service.update_agent(project, old_id, AgentUpdate(name="新昵称", instruction="旧职责", model="fixture"),
        enrich_resources=False)
    await service.update_agent(project, target_id, AgentUpdate(instruction="新草稿职责"), enrich_resources=False)
    gate.assert_not_awaited()
    assert (await state(db, old_id)).is_active
    assert (await state(db, target_id)).instruction == "新草稿职责"


@pytest.mark.asyncio
async def test_live_business_edit_cannot_reuse_old_proof_but_explicit_pause_can_save_it(fixture):
    db, service, project, _, old_id, _, _, gate, _ = fixture
    with pytest.raises(AuthorizationError):
        await service.update_agent(project, old_id, AgentUpdate(instruction="未经测试的新职责"), enrich_resources=False)
    assert (await state(db, old_id)).instruction == "旧职责"
    await service.update_agent(project, old_id, AgentUpdate(is_active=False, instruction="待测试的新职责"),
        enrich_resources=False)
    assert not (await state(db, old_id)).is_active
    assert (await state(db, old_id)).instruction == "待测试的新职责"
    gate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["collection", "workflow", "tool"])
async def test_live_binding_cannot_be_enabled_by_a_separate_toggle(fixture, kind):
    db, service, project, _, old_id, _, _, _, _ = fixture
    resource = uuid4()
    if kind == "collection":
        binding = AgentCollection(agent_id=old_id, collection_id=str(resource), enabled=False)
        operation = service.set_collection_enabled
        argument = str(resource)
    elif kind == "workflow":
        binding = AgentWorkflow(agent_id=old_id, workflow_id=str(resource), enabled=False)
        operation = service.set_workflow_enabled
        argument = str(resource)
    else:
        db.add(Tool(id=resource, project_id=project, name="隔离工具", tool_type=ToolType.FUNCTION))
        binding = AgentToolAssociation(agent_id=old_id, tool_id=resource, enabled=False)
        operation = service.set_tool_enabled
        argument = resource
    db.add(binding)
    await db.commit()
    with pytest.raises(ConflictError):
        await operation(project, old_id, argument, True)
    await db.refresh(binding)
    assert binding.enabled is False and (await state(db, old_id)).is_active
    # An old in-memory binding must not bypass a concurrent revocation.
    binding.enabled = True
    await db.commit()
    await db.execute(update(type(binding)).where(type(binding).id == binding.id)
        .values(enabled=False).execution_options(synchronize_session=False))
    await db.commit()
    assert binding.enabled is True
    with pytest.raises(ConflictError):
        await operation(project, old_id, argument, True)
    await db.refresh(binding)
    assert binding.enabled is False
    # Revocation must remain available even for a currently serving employee.
    binding.enabled = True
    await db.commit()
    await operation(project, old_id, argument, False)
    await db.refresh(binding)
    assert binding.enabled is False


@pytest.mark.asyncio
async def test_conflicting_pause_and_default_switch_cannot_clear_the_current_default(fixture):
    db, service, project, _, old_id, target_id, _, _, _ = fixture
    with pytest.raises(TGOAIServiceException):
        await service.update_agent(project, target_id, AgentUpdate(is_active=False, is_default=True),
            enrich_resources=False)
    assert (await state(db, old_id)).is_default


@pytest.mark.asyncio
async def test_internal_default_switch_also_requires_current_actor_proof(fixture):
    db, service, project, _, old_id, target_id, _, _, _ = fixture
    with pytest.raises(AuthorizationError):
        await service._replace_project_default_agent(project, target_id)
    assert (await state(db, old_id)).is_active and (await state(db, old_id)).is_default
    assert not (await state(db, target_id)).is_active
