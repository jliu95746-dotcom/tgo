"""The customer reply and assist training must resolve the same style."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.employee_style import resolve_employee_style


@pytest.mark.asyncio
async def test_default_employee_binding(monkeypatch):
    client = SimpleNamespace(list_agents=AsyncMock(return_value={"data": [{
        "id": "employee", "name": "价格专员", "humanization_skill_name": "brand-style",
        "humanization_skill_enabled": True}]}))
    monkeypatch.setattr("app.services.employee_style.ai_client", client)
    setting = await resolve_employee_style("project", None)
    assert setting.skill_name == "brand-style"
    assert setting.enabled and setting.source == "employee"
    assert setting.agent_id == "employee"
    client.list_agents.assert_awaited_once_with("project", is_default=True, limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_existing_conversation_override_preserved(monkeypatch, enabled):
    client = SimpleNamespace(get_agent=AsyncMock())
    monkeypatch.setattr("app.services.employee_style.ai_client", client)
    setting = await resolve_employee_style("project", "employee", "other-style", enabled)
    assert setting.skill_name == "other-style"
    assert setting.enabled is enabled
    assert setting.source == "conversation"
    client.get_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_employee_style_not_used(monkeypatch):
    monkeypatch.setattr("app.services.employee_style.ai_client", SimpleNamespace(
        get_agent=AsyncMock(return_value={"id": "employee", "name": "物流专员",
            "humanization_skill_name": "brand-style", "humanization_skill_enabled": False})))
    setting = await resolve_employee_style("project", "employee")
    assert setting.skill_name == "brand-style" and not setting.enabled


@pytest.mark.asyncio
async def test_customer_routing_pins_resolved_default_employee_for_both_phases(monkeypatch):
    from app.api.v1.endpoints import chat
    from app.schemas.employee_style import EmployeeStyle
    resolver = AsyncMock(return_value=EmployeeStyle(agent_id="default-employee", skill_name="brand-style", enabled=True))
    monkeypatch.setattr(chat, "resolve_employee_style", resolver)
    result = await chat._resolve_platform_agent_kwargs(
        SimpleNamespace(type="website", agent_id=None),
        SimpleNamespace(project_id="project", humanization_skill_name=None, humanization_skill_enabled=False))
    assert result["agent_id"] == "default-employee"
    assert result["humanization_skill_name"] == "brand-style"
    assert result["knowledge_channel"] == "web"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", [None, "paused"])
@pytest.mark.parametrize("override", [None, "conversation-style"])
async def test_automatic_reply_rejects_inactive_employee(
    monkeypatch, agent_id, override,
):
    from app.services.employee_style import InactiveEmployeeError

    inactive = {"id": "paused", "is_active": False}
    client = SimpleNamespace(
        get_agent=AsyncMock(return_value=inactive),
        list_agents=AsyncMock(return_value={"data": [inactive]}),
    )
    monkeypatch.setattr("app.services.employee_style.ai_client", client)
    with pytest.raises(InactiveEmployeeError):
        await resolve_employee_style(
            "project", agent_id, override, True, require_active=True,
        )


@pytest.mark.asyncio
async def test_automatic_reply_requires_an_employee(monkeypatch):
    from app.services.employee_style import InactiveEmployeeError

    monkeypatch.setattr("app.services.employee_style.ai_client", SimpleNamespace(
        list_agents=AsyncMock(return_value={"data": []}),
    ))
    with pytest.raises(InactiveEmployeeError):
        await resolve_employee_style("project", None, require_active=True)


@pytest.mark.asyncio
async def test_active_employee_keeps_conversation_style_and_resolved_id(monkeypatch):
    monkeypatch.setattr("app.services.employee_style.ai_client", SimpleNamespace(
        list_agents=AsyncMock(return_value={"data": [{
            "id": "enabled", "name": "女包客服", "is_active": True,
        }]}),
    ))
    setting = await resolve_employee_style(
        "project", None, "conversation-style", False, require_active=True,
    )
    assert setting.agent_id == "enabled"
    assert setting.agent_name == "女包客服"
    assert setting.source == "conversation" and not setting.enabled
    assert setting.skill_name == "conversation-style"
