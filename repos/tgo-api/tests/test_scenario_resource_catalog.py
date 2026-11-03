"""Resource outages, foreign records, and configuration never become test success."""

from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.scenario_resources import ResourceSection
from app.services import scenario_resources as resources


def mock_sources(monkeypatch, project):
    monkeypatch.setattr(resources, "connection_catalog", Mock(return_value=ResourceSection(kind="connection")))
    monkeypatch.setattr(resources, "model_catalog", Mock(return_value=ResourceSection(kind="model")))
    monkeypatch.setattr(resources.rag_client, "knowledge_availability", AsyncMock(return_value={
        "project_id": str(project), "channel": "web", "collections": [],
    }))
    monkeypatch.setattr(resources.ai_client, "list_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(resources.workflow_client, "list_workflows", AsyncMock(return_value={"data": [], "pagination": {"has_next": False}}))
    monkeypatch.setattr(resources.device_control_client, "list_devices", AsyncMock(return_value={"devices": [], "total": 0}))


@pytest.mark.asyncio
async def test_errors_are_explicit_and_do_not_report_missing_resources(monkeypatch, caplog):
    project = uuid4()
    mock_sources(monkeypatch, project)
    monkeypatch.setattr(resources.ai_client, "list_tools", AsyncMock(side_effect=OSError("private-upstream-token")))
    catalog = await resources.load_catalog(Mock(), project)
    sections = {section.kind: section for section in catalog.sections}
    assert sections["tool"].status == "unavailable"
    assert sections["knowledge"].status == "available"
    assert sections["connection"].status == "available"
    assert "private-upstream-token" not in catalog.model_dump_json() + caplog.text
    assert catalog.project_id == project


@pytest.mark.asyncio
async def test_catalog_enforces_scope_and_projects_only_safe_metadata(monkeypatch):
    project, tool_id, knowledge_id = uuid4(), uuid4(), uuid4()
    mock_sources(monkeypatch, project)
    monkeypatch.setattr(resources.ai_client, "list_tools", AsyncMock(return_value=[{
        "id": str(tool_id), "project_id": str(project), "name": "订单工具",
        "config": {"api_key": "private-credential"}, "endpoint": "private-url",
    }]))
    monkeypatch.setattr(resources.rag_client, "knowledge_availability", AsyncMock(return_value={
        "project_id": str(project), "channel": "web", "collections": [{
            "id": str(knowledge_id), "name": "待处理资料", "eligible_chunk_count": 0,
            "total_chunk_count": 0, "blocked_reasons": [],
        }],
    }))
    catalog = await resources.load_catalog(Mock(), project)
    sections = {section.kind: section for section in catalog.sections}
    assert sections["tool"].items[0].status == "configured"
    assert sections["knowledge"].items[0].status == "not_ready"
    assert "private-credential" not in catalog.model_dump_json()
    assert "private-url" not in catalog.model_dump_json()
    resources.ai_client.list_tools.assert_awaited_once_with(project_id=str(project))
    resources.device_control_client.list_devices.assert_awaited_once_with(project_id=str(project), limit=100)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["tool", "workflow", "device", "knowledge"])
async def test_foreign_records_make_the_source_unavailable(monkeypatch, source):
    project = uuid4()
    mock_sources(monkeypatch, project)
    foreign = {"id": str(uuid4()), "project_id": str(uuid4()), "name": "foreign-resource"}
    if source == "tool":
        monkeypatch.setattr(resources.ai_client, "list_tools", AsyncMock(return_value=[foreign]))
    elif source == "workflow":
        monkeypatch.setattr(resources.workflow_client, "list_workflows", AsyncMock(return_value={"data": [{**foreign, "status": "active", "version": 2}], "pagination": {"has_next": False}}))
    elif source == "device":
        monkeypatch.setattr(resources.device_control_client, "list_devices", AsyncMock(return_value={"devices": [{**foreign, "device_name": foreign["name"], "status": "online"}], "total": 1}))
    else:
        monkeypatch.setattr(resources.rag_client, "knowledge_availability", AsyncMock(return_value={"project_id": str(uuid4()), "channel": "web", "collections": []}))
    section = next(section for section in (await resources.load_catalog(Mock(), project)).sections if section.kind == source)
    assert section.status == "unavailable"
    assert section.items == []


@pytest.mark.asyncio
async def test_catalog_does_not_claim_a_partial_page_is_complete(monkeypatch):
    project = uuid4()
    mock_sources(monkeypatch, project)
    monkeypatch.setattr(resources.workflow_client, "list_workflows", AsyncMock(return_value={"data": [], "pagination": {"has_next": True}}))
    result = await resources.load_catalog(Mock(), project)
    assert next(section for section in result.sections if section.kind == "workflow").status == "partial"


def test_configured_default_pending_sync_is_reported_as_selected_but_not_ready():
    project, provider_id = uuid4(), uuid4()
    provider = SimpleNamespace(id=provider_id, project_id=project, sync_status="synced", is_active=True)
    model = SimpleNamespace(id=uuid4(), model_name="默认聊天模型", model_type="chat", model_id="chat-model", is_active=True)
    db = Mock()
    db.execute.return_value.all.return_value = [(model, provider)]
    db.scalar.return_value = SimpleNamespace(default_chat_provider_id=provider_id, default_chat_model="chat-model", sync_status="pending")
    section = resources.model_catalog(db, project)
    assert section.items[0].details.is_default is True
    assert section.items[0].status == "not_ready"


def test_foreign_model_provider_is_rejected_even_if_upstream_query_is_incorrect():
    db = Mock()
    provider = SimpleNamespace(project_id=uuid4())
    db.execute.return_value.all.return_value = [(Mock(), provider)]
    with pytest.raises(ValueError, match="Model scope mismatch"):
        resources.model_catalog(db, uuid4())


@pytest.mark.asyncio
async def test_workflow_catalog_uses_the_fixed_release_instead_of_editable_version(monkeypatch):
    project = uuid4()
    mock_sources(monkeypatch, project)
    monkeypatch.setattr(resources.workflow_client, "list_workflows", AsyncMock(return_value={
        "data": [{"id": str(uuid4()), "project_id": str(project), "name": "流程", "status": "active", "version": 9, "published_version": 2}],
        "pagination": {"has_next": False},
    }))
    section = await resources._workflow_catalog(project)
    assert section.items[0].status == "configured"
    assert section.items[0].details.workflow_version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, "a" * 64])
async def test_knowledge_revision_is_preserved_and_missing_owner_revision_stays_unknown(monkeypatch, revision):
    project, collection = uuid4(), uuid4()
    mock_sources(monkeypatch, project)
    row = {"id": str(collection), "name": "澜月资料", "eligible_chunk_count": 2,
           "total_chunk_count": 2, "blocked_reasons": []}
    if revision is not None:
        row["resource_revision"] = revision
    monkeypatch.setattr(resources.rag_client, "knowledge_availability", AsyncMock(return_value={
        "project_id": str(project), "channel": "web", "collections": [row],
    }))
    result = await resources._knowledge_catalog(project, resources.KnowledgeChannel.WEB)
    assert result.items[0].details.resource_revision == revision


@pytest.mark.asyncio
async def test_malformed_owner_revision_is_not_a_configured_resource(monkeypatch):
    project = uuid4()
    mock_sources(monkeypatch, project)
    monkeypatch.setattr(resources.rag_client, "knowledge_availability", AsyncMock(return_value={
        "project_id": str(project), "channel": "web", "collections": [{
            "id": str(uuid4()), "name": "资料", "eligible_chunk_count": 2,
            "total_chunk_count": 2, "resource_revision": "pretend-passed",
        }],
    }))
    catalog = await resources.load_catalog(Mock(), project)
    section = next(section for section in catalog.sections if section.kind == "knowledge")
    assert section.status == "unavailable" and section.items == []
