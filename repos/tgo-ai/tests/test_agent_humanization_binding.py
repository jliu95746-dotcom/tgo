"""Employee style bindings are optional, project scoped and independently enabled."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.exceptions import ValidationError
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_service import AgentService


@pytest.mark.asyncio
async def test_creation_without_style_does_not_create_a_skill():
    db = Mock(flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    agent = await AgentService(db).create_agent(uuid4(), AgentCreate(name="价格专员", model="fixture"))
    assert agent.humanization_skill_name is None
    assert agent.humanization_skill_enabled is False


@pytest.mark.asyncio
async def test_create_validates_and_persists_binding(monkeypatch):
    validate = AsyncMock()
    monkeypatch.setattr("app.services.agent_service.validate_humanization_binding", validate)
    db = Mock(flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    project = uuid4()
    agent = await AgentService(db).create_agent(project, AgentCreate(
        name="价格专员", model="fixture", humanization_skill_name="brand-style",
        humanization_skill_enabled=True, skills_enabled=False))
    validate.assert_awaited_once_with(project, "brand-style", True)
    assert agent.humanization_skill_name == "brand-style"
    assert agent.humanization_skill_enabled is True
    assert agent.skills_enabled is False


@pytest.mark.asyncio
async def test_switch_off_keeps_binding_and_other_settings(monkeypatch):
    monkeypatch.setattr("app.services.agent_service.validate_humanization_binding", AsyncMock())
    agent = SimpleNamespace(humanization_skill_name="brand-style", humanization_skill_enabled=True)
    service = AgentService(Mock(commit=AsyncMock(), refresh=AsyncMock()))
    service.get_agent = AsyncMock(return_value=agent)
    await service.update_agent(uuid4(), uuid4(), AgentUpdate(humanization_skill_enabled=False))
    assert agent.humanization_skill_name == "brand-style"
    assert agent.humanization_skill_enabled is False


@pytest.mark.asyncio
async def test_cannot_enable_without_a_skill():
    from app.services.agent_humanization import validate_humanization_binding
    with pytest.raises(ValidationError):
        await validate_humanization_binding(uuid4(), None, True)


@pytest.mark.asyncio
async def test_ordinary_skill_rejected(monkeypatch):
    from app.services.agent_humanization import validate_humanization_binding
    monkeypatch.setattr("app.services.agent_humanization.SkillFileService.get_skill",
                        AsyncMock(return_value=SimpleNamespace(skill_type="standard")))
    with pytest.raises(ValidationError):
        await validate_humanization_binding(uuid4(), "ordinary-skill", True)


@pytest.mark.asyncio
async def test_validation_uses_the_owning_project(monkeypatch):
    from app.services.agent_humanization import validate_humanization_binding
    lookup = AsyncMock(return_value=SimpleNamespace(skill_type="humanization"))
    monkeypatch.setattr("app.services.agent_humanization.SkillFileService.get_skill", lookup)
    project = uuid4()
    await validate_humanization_binding(project, "brand-style", True)
    lookup.assert_awaited_once_with(str(project), "brand-style")


@pytest.mark.asyncio
async def test_shared_usage_keeps_employee_switches_independent():
    from app.services.agent_humanization import humanization_usage
    db = SimpleNamespace(execute=AsyncMock(return_value=[
        (uuid4(), "价格专员", "brand-style", True),
        (uuid4(), "物流专员", "brand-style", False),
    ]))
    project = uuid4()
    result = await humanization_usage(db, str(project))
    assert [employee.enabled for employee in result["brand-style"]] == [True, False]
    query = db.execute.call_args.args[0]
    assert project in query.compile().params.values()
    assert "deleted_at IS NULL" in str(query)


@pytest.mark.asyncio
async def test_bound_skill_cannot_be_deleted(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1 import skills
    deletion = AsyncMock()
    monkeypatch.setattr(skills, "_get_skill_service", lambda: SimpleNamespace(delete_skill=deletion))
    monkeypatch.setattr(skills, "humanization_usage", AsyncMock(return_value={"brand-style": [object()]}))
    with pytest.raises(HTTPException) as raised:
        await skills.delete_skill("brand-style", str(uuid4()), db=Mock())
    assert raised.value.status_code == 409
    deletion.assert_not_awaited()
