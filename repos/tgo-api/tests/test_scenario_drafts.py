"""Durable setup drafts must not overwrite other users or concurrent edits."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.business_scenarios import router
from app.core.database import get_db
from app.core.exceptions import ConflictError, NotFoundError, TGOAPIException, tgo_api_exception_handler, validation_exception_handler
from app.core.security import get_current_active_user
from app.models.scenario_draft import ScenarioDraft
from app.schemas.scenario_drafts import ScenarioDraftCreate, ScenarioDraftUpdate
from app.services import scenario_drafts as drafts


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    ScenarioDraft.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def request(**overrides):
    return ScenarioDraftCreate(idempotency_key=uuid4(), template_id="knowledge", module="agents", **overrides)


def test_saved_draft_survives_session_restart_and_keeps_user_configuration(db):
    project, staff = uuid4(), uuid4()
    draft = drafts.create_draft(db, project, staff, request())
    changed = drafts.update_draft(db, project, staff, draft.id, ScenarioDraftUpdate(
        expected_revision=draft.revision, step="resources",
        business={"name": "澜月商品客服", "profession": "商品咨询", "company_summary": "女包品牌", "instructions": "只根据企业资料回答。"},
        bindings={"knowledge_ids": [str(uuid4())]},
        agent_settings={"tool_call_limit": 7, "humanization_skill_enabled": True, "markdown": False},
    ))
    db.expire_all()
    with Session(db.get_bind()) as reopened:
        restored = drafts.get_draft(reopened, project, staff, draft.id)
    assert restored.revision == changed.revision == 2
    assert restored.business.name == "澜月商品客服"
    assert restored.business.company_summary == "女包品牌"
    assert restored.bindings.knowledge_ids == changed.bindings.knowledge_ids
    assert restored.bindings.model is None  # Keep following the project default.
    assert restored.step == "resources"
    assert restored.template_version == 1
    assert restored.status == "draft"
    assert restored.agent_settings.tool_call_limit == 7
    assert restored.agent_settings.humanization_skill_enabled is True
    assert restored.agent_settings.markdown is False


def test_partial_business_changes_preserve_the_other_saved_fields(db):
    project, staff = uuid4(), uuid4()
    row = drafts.create_draft(db, project, staff, request(business={"company_summary": "企业信息"}))
    saved = drafts.update_draft(db, project, staff, row.id, ScenarioDraftUpdate(expected_revision=1, business={"name": "自定义名字"}))
    assert saved.business.name == "自定义名字"
    assert saved.business.company_summary == "企业信息"
    assert saved.business.instructions == row.business.instructions


def test_retried_creation_returns_current_saved_draft_without_resetting_it(db):
    project, staff = uuid4(), uuid4()
    payload = request()
    original = drafts.create_draft(db, project, staff, payload)
    drafts.update_draft(db, project, staff, original.id, ScenarioDraftUpdate(expected_revision=1, business={"name": "用户修改"}))
    again = drafts.create_draft(db, project, staff, payload)
    assert again.id == original.id
    assert again.business.name == "用户修改"
    assert again.revision == 2
    assert len(db.scalars(select(ScenarioDraft)).all()) == 1
    with pytest.raises(ConflictError):
        drafts.create_draft(db, project, staff, payload.model_copy(update={"channel": "wecom_kf"}))


def test_stale_editor_cannot_overwrite_a_newer_save(db):
    project, staff = uuid4(), uuid4()
    row = drafts.create_draft(db, project, staff, request())
    drafts.update_draft(db, project, staff, row.id, ScenarioDraftUpdate(expected_revision=1, business={"name": "较新配置"}))
    with pytest.raises(ConflictError):
        drafts.update_draft(db, project, staff, row.id, ScenarioDraftUpdate(expected_revision=1, business={"name": "旧页面配置"}))
    assert drafts.get_draft(db, project, staff, row.id).business.name == "较新配置"


def test_drafts_are_scoped_to_the_authenticated_enterprise_and_creator(db):
    project, staff = uuid4(), uuid4()
    row = drafts.create_draft(db, project, staff, request())
    for foreign_project, foreign_staff in [(uuid4(), staff), (project, uuid4())]:
        assert drafts.list_drafts(db, foreign_project, foreign_staff) == []
        with pytest.raises(NotFoundError):
            drafts.get_draft(db, foreign_project, foreign_staff, row.id)
        with pytest.raises(NotFoundError):
            drafts.update_draft(db, foreign_project, foreign_staff, row.id, ScenarioDraftUpdate(expected_revision=1, step="testing"))


@pytest.mark.parametrize("payload", [
    {"template_version": 999}, {"template_id": "files", "module": "agents"}, {"template_id": "screen", "module": "devices"},
])
def test_unknown_or_unavailable_template_cannot_create_an_actionable_draft(db, payload):
    with pytest.raises((ConflictError, NotFoundError)):
        drafts.create_draft(db, uuid4(), uuid4(), ScenarioDraftCreate(idempotency_key=uuid4(), template_id="knowledge", module="agents").model_copy(update=payload))
    assert not db.scalars(select(ScenarioDraft)).all()


@pytest.mark.parametrize("payload", [
    {"project_id": str(uuid4())}, {"status": "active"}, {"business": {"api_key": "private-fixture"}},
    {"bindings": {"toolConfigs": {"authorization": "private-fixture"}}},
])
def test_draft_payload_rejects_scope_overrides_credentials_and_activation(payload):
    with pytest.raises(ValidationError):
        ScenarioDraftCreate.model_validate({"idempotency_key": str(uuid4()), "template_id": "knowledge", "module": "agents", **payload})


@pytest.mark.asyncio
async def test_staff_without_configuration_role_cannot_write_drafts(db):
    app = FastAPI()
    app.include_router(router, prefix="/v1/business-scenarios")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=uuid4(), project_id=uuid4(), role="user", username="test-staff")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/business-scenarios/drafts", json=request().model_dump(mode="json"))
    assert response.status_code == 403
    assert not db.scalars(select(ScenarioDraft)).all()


@pytest.mark.asyncio
async def test_template_catalog_reports_configuration_support_and_test_cases():
    app = FastAPI()
    app.include_router(router, prefix="/v1/business-scenarios")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/business-scenarios/templates?module=agents")
    assert response.status_code == 200
    assert len(response.json()) == 4
    assert all(len(template["test_questions"]) >= 3 for template in response.json())
    assert all(template["version"] == 1 for template in response.json())
    assert all(template["default_model_mode"] == "system_default" for template in response.json())


@pytest.mark.asyncio
async def test_authenticated_draft_http_contract_includes_revision_conflict_and_scope(db):
    app = FastAPI()
    app.include_router(router, prefix="/v1/business-scenarios")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    identity = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin", username="test-admin")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: identity
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/v1/business-scenarios/drafts", json=request().model_dump(mode="json"))
        assert created.status_code == 201
        saved = created.json()
        assert saved["status"] == "draft"
        assert "request_digest" not in saved and "idempotency_key" not in saved
        path = f"/v1/business-scenarios/drafts/{saved['id']}"
        updated = await client.patch(path, json={"expected_revision": 1, "business": {"name": "用户名字"}})
        assert updated.status_code == 200 and updated.json()["revision"] == 2
        conflict = await client.patch(path, json={"expected_revision": 1, "business": {"name": "旧页面"}})
        assert conflict.status_code == 409
        identity = SimpleNamespace(id=identity.id, project_id=uuid4(), role="admin", username="other-admin")
        assert (await client.get(path)).status_code == 404
        assert (await client.get("/v1/business-scenarios/drafts")).json() == []
        assert (await client.patch(path, json={"expected_revision": 2, "step": "testing"})).status_code == 404


@pytest.mark.asyncio
async def test_draft_validation_errors_do_not_echo_or_log_rejected_credentials(db, caplog):
    app = FastAPI()
    app.include_router(router, prefix="/v1/business-scenarios")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin", username="test-admin")
    payload = request().model_dump(mode="json")
    payload["business"]["api_key"] = "private-rejected-draft-fixture"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/business-scenarios/drafts", json=payload)
    assert response.status_code == 422
    assert "private-rejected-draft-fixture" not in response.text + caplog.text
