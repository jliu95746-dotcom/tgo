"""Readiness checks validate actual bindings without claiming a successful business test."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.schemas.scenario_readiness import ScenarioReadinessRequest
from app.schemas.scenario_resources import ScenarioResourceCatalog
from app.services import scenario_readiness as readiness


def catalog(project, knowledge_status="configured", knowledge_source="available"):
    provider = uuid4()
    knowledge = uuid4()
    result = ScenarioResourceCatalog(
        project_id=project, channel="web", checked_at=datetime.now(timezone.utc), sections=[
            {"kind": "model", "items": [{"id": str(uuid4()), "kind": "model", "name": "默认模型", "status": "configured", "details": {
                "provider_id": str(provider), "model_id": "default-chat", "purpose": "chat", "is_default": True,
            }}]},
            {"kind": "knowledge", "status": knowledge_source, "items": [{"id": str(knowledge), "kind": "knowledge", "name": "企业资料", "status": knowledge_status}]},
            {"kind": "tool"}, {"kind": "workflow"}, {"kind": "device"},
            {"kind": "connection", "status": "not_supported"},
        ],
    )
    return result, knowledge


def test_inherits_configured_default_but_never_marks_the_business_test_passed():
    data, knowledge = catalog(uuid4())
    report = readiness.build_report(data, ScenarioReadinessRequest(template_id="knowledge", bindings={"knowledge_ids": [str(knowledge)]}))
    assert report.configuration_ready is True
    assert report.effective_bindings.model.model_id == "default-chat"
    assert report.test_status == "not_tested"
    assert report.can_enable is False


@pytest.mark.parametrize("state", ["not_ready", "offline", "disabled"])
def test_unavailable_selected_resources_are_configuration_issues(state):
    data, knowledge = catalog(uuid4(), knowledge_status=state)
    report = readiness.build_report(data, ScenarioReadinessRequest(template_id="knowledge", bindings={"knowledge_ids": [str(knowledge)]}))
    assert report.configuration_ready is False
    assert any(issue.code == "not_ready" and issue.kind == "knowledge" for issue in report.issues)


def test_source_outage_is_not_an_instruction_to_create_duplicate_resources():
    data, _ = catalog(uuid4(), knowledge_source="unavailable")
    report = readiness.build_report(data, ScenarioReadinessRequest(template_id="knowledge"))
    issues = [issue for issue in report.issues if issue.kind == "knowledge"]
    assert [issue.code for issue in issues] == ["unavailable"]


def test_unknown_selection_cannot_silently_fall_back_to_the_system_model():
    data, knowledge = catalog(uuid4())
    report = readiness.build_report(data, ScenarioReadinessRequest(template_id="knowledge", bindings={
        "model": {"provider_id": str(uuid4()), "model_id": "foreign-model"}, "knowledge_ids": [str(knowledge)],
    }))
    assert report.configuration_ready is False
    assert any(issue.kind == "model" and issue.code == "invalid_binding" for issue in report.issues)


def test_unknown_binding_on_a_partial_page_is_unverified_not_missing():
    data, knowledge = catalog(uuid4())
    workflow = next(section for section in data.sections if section.kind == "workflow")
    workflow.status = "partial"
    report = readiness.build_report(data, ScenarioReadinessRequest(template_id="knowledge", bindings={
        "knowledge_ids": [str(knowledge)], "workflow_ids": [str(uuid4())],
    }))
    assert any(issue.kind == "workflow" and issue.code == "unverified" for issue in report.issues)


@pytest.mark.asyncio
async def test_offline_or_foreign_device_never_triggers_a_capability_request(monkeypatch):
    project = uuid4()
    data, _ = catalog(project)
    device_id = uuid4()
    next(section for section in data.sections if section.kind == "device").items = []
    monkeypatch.setattr(readiness, "load_catalog", AsyncMock(return_value=data))
    tools = AsyncMock()
    monkeypatch.setattr(readiness.device_control_client, "get_device_tools", tools)
    report = await readiness.check_scenario(Mock(), project, ScenarioReadinessRequest(template_id="files", bindings={"device_ids": [str(device_id)]}))
    assert report.configuration_ready is False
    tools.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("template,names,issue", [
    ("files", ["fs_read"], None),
    ("report", ["shell_exec"], None),
    ("screen", ["screenshot", "click"], "capability_missing"),
    ("screen", ["screenshot", "click", "type_text"], None),
    ("files", [], "capability_missing"),
])
async def test_online_device_checks_reported_capabilities_without_executing(template, names, issue, monkeypatch):
    project, device_id = uuid4(), uuid4()
    data, _ = catalog(project)
    device = next(section for section in data.sections if section.kind == "device")
    from app.schemas.scenario_resources import ScenarioResource
    device.items = [ScenarioResource(id=str(device_id), kind="device", name="受控测试设备", status="configured")]
    monkeypatch.setattr(readiness, "load_catalog", AsyncMock(return_value=data))
    tools = AsyncMock(return_value={"tools": [{"name": name} for name in names]})
    monkeypatch.setattr(readiness.device_control_client, "get_device_tools", tools)
    execute = AsyncMock()
    monkeypatch.setattr(readiness.device_control_client, "execute_device_tools", execute, raising=False)
    report = await readiness.check_scenario(Mock(), project, ScenarioReadinessRequest(template_id=template, bindings={"device_ids": [str(device_id)]}))
    tools.assert_awaited_once_with(str(device_id), str(project))
    execute.assert_not_awaited()
    assert report.configuration_ready is (issue is None)
    assert [item.code for item in report.issues] == ([] if issue is None else [issue])
    assert report.can_enable is False


@pytest.mark.asyncio
async def test_device_capability_timeout_is_unavailable_not_missing(monkeypatch):
    project, device_id = uuid4(), uuid4()
    data, _ = catalog(project)
    from app.schemas.scenario_resources import ScenarioResource
    next(section for section in data.sections if section.kind == "device").items = [
        ScenarioResource(id=str(device_id), kind="device", name="受控测试设备", status="configured"),
    ]
    monkeypatch.setattr(readiness, "load_catalog", AsyncMock(return_value=data))
    monkeypatch.setattr(readiness.device_control_client, "get_device_tools", AsyncMock(side_effect=TimeoutError()))
    report = await readiness.check_scenario(Mock(), project, ScenarioReadinessRequest(template_id="files", bindings={"device_ids": [str(device_id)]}))
    assert report.configuration_ready is False
    assert [item.code for item in report.issues] == ["unavailable"]
