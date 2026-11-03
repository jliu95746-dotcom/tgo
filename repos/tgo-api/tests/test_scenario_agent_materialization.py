"""Only a login-owned, persisted draft can create its inactive employee."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.scenario_draft import ScenarioDraft
from app.schemas.scenario_drafts import ScenarioDraftCreate
from app.schemas.scenario_agents import ScenarioAgentReceipt
from app.services import scenario_agents, scenario_drafts
from app.services.ai_client import AIServiceClient
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.api.v1.endpoints.business_scenarios import router
from app.core.exceptions import validation_exception_handler


@pytest.fixture
def setup_db():
    engine = create_engine("sqlite:///:memory:")
    ScenarioDraft.__table__.create(engine)
    with Session(engine) as db:
        project, staff = uuid4(), uuid4()
        draft = scenario_drafts.create_draft(db, project, staff, ScenarioDraftCreate(
            idempotency_key=uuid4(), module="agents", template_id="knowledge",
            business={"name": "澜月客服", "profession": "商品咨询", "company_summary": "澜月女包", "instructions": "依据资料回答"},
        ))
        yield db, project, staff, draft
    engine.dispose()


@pytest.mark.asyncio
async def test_materialization_uses_saved_snapshot_and_trusted_identity(setup_db, monkeypatch):
    db, project, staff, draft = setup_db
    receipt = ScenarioAgentReceipt(project_id=project, created_by=staff, draft_id=draft.id,
                                   revision=draft.revision, agent_id=uuid4(), is_active=False)
    client = SimpleNamespace(save_agent_draft=AsyncMock(return_value=receipt))
    monkeypatch.setattr(scenario_agents, "ai_client", client)
    assert await scenario_agents.save(db, project, staff, draft.id, draft.revision) == receipt
    args = client.save_agent_draft.await_args.args
    assert args[:3] == (project, staff, draft.id)
    snapshot = args[3]
    assert snapshot.revision == draft.revision
    assert snapshot.agent.name == "澜月客服"
    assert snapshot.agent.model == "__system_default__"
    assert snapshot.agent.is_active is False and snapshot.agent.is_default is False
    assert snapshot.agent.config["company_summary"] == "澜月女包"
    assert snapshot.activation_selection.template_id == draft.template_id
    assert snapshot.activation_selection.template_version == draft.template_version
    assert snapshot.activation_selection.channel == draft.channel


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [None, {"template_id": "aftersales", "template_version": 1, "channel": "web"}])
async def test_client_rejects_missing_or_changed_saved_role(setup_db, monkeypatch, selection):
    db, project, staff, draft = setup_db
    receipt = ScenarioAgentReceipt(project_id=project, created_by=staff, draft_id=draft.id,
                                   revision=1, agent_id=uuid4(), is_active=False)
    client = AIServiceClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=httpx.Response(
        200, json={**receipt.model_dump(mode="json"), "activation_selection": selection},
    )))
    with pytest.raises(HTTPException) as error:
        await client.save_agent_draft(project, staff, draft.id, scenario_agents._snapshot(db, draft))
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_stale_or_foreign_draft_never_contacts_ai(setup_db, monkeypatch):
    db, project, staff, draft = setup_db
    client = SimpleNamespace(save_agent_draft=AsyncMock())
    monkeypatch.setattr(scenario_agents, "ai_client", client)
    for p, s, revision, error in [(uuid4(), staff, 1, NotFoundError),
                                  (project, uuid4(), 1, NotFoundError),
                                  (project, staff, 2, ConflictError)]:
        with pytest.raises(error):
            await scenario_agents.save(db, p, s, draft.id, revision)
    client.save_agent_draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"project_id": "foreign"}, {"created_by": "foreign"},
                                  {"draft_id": "foreign"}, {"revision": 99}, {"can_enable": True}])
async def test_client_rejects_wrong_scope_revision_and_approval(setup_db, monkeypatch, change):
    db, project, staff, draft = setup_db
    receipt = ScenarioAgentReceipt(project_id=project, created_by=staff, draft_id=draft.id,
                                   revision=1, agent_id=uuid4(), is_active=False,
                                   activation_selection=scenario_agents._snapshot(db, draft).activation_selection)
    client = AIServiceClient()
    transport = AsyncMock(return_value=httpx.Response(200, json={**receipt.model_dump(mode="json"), **change}))
    monkeypatch.setattr(client, "_make_request", transport)
    with pytest.raises(HTTPException) as error:
        await client.save_agent_draft(project, staff, draft.id, scenario_agents._snapshot(db, draft))
    assert error.value.status_code == 502
    assert "foreign" not in str(error.value.detail)
    assert transport.await_args.kwargs["extra_headers"]["X-Staff-Id"] == str(staff)


@pytest.mark.asyncio
async def test_client_returns_saved_role_without_granting_activation(setup_db, monkeypatch):
    db, project, staff, draft = setup_db
    snapshot = scenario_agents._snapshot(db, draft)
    receipt = ScenarioAgentReceipt(project_id=project, created_by=staff, draft_id=draft.id,
        revision=draft.revision, agent_id=uuid4(), is_active=False,
        activation_selection=snapshot.activation_selection)
    client = AIServiceClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=httpx.Response(
        200, json=receipt.model_dump(mode="json"))))
    assert await client.save_agent_draft(project, staff, draft.id, snapshot) == receipt
    assert receipt.can_enable is False


@pytest.mark.asyncio
async def test_public_route_denies_non_admin_and_redacts_rejected_body(setup_db):
    db, project, staff, draft = setup_db
    app = FastAPI()
    app.include_router(router, prefix="/v1/business-scenarios")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=staff, project_id=project, role="user", username="fixture",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(f"/v1/business-scenarios/drafts/{draft.id}/agent", json={"expected_revision": 1})).status_code == 403
        assert (await client.get(f"/v1/business-scenarios/drafts/{draft.id}/agent")).status_code == 403
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id=staff, project_id=project, role="admin", username="fixture",
        )
        marker = "do-not-echo-private-fixture"
        response = await client.post(f"/v1/business-scenarios/drafts/{draft.id}/agent", json={"expected_revision": 1, "secret": marker})
        assert response.status_code == 422 and marker not in response.text
