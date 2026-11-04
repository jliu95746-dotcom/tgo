"""The template owner supplies complete requirements to trusted AI calls."""

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.api.internal.endpoints.agent_activation import router
from app.core.config import settings
from app.core.exceptions import (
    TGOAPIException,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.database import get_db
from app.models.project import Project
from app.models.staff import Staff
from app.schemas.agent_activation import AgentActivationRequirements
from app.services import agent_activation_requirements
from app.services.knowledge_review_cases import list_cases


@pytest.fixture
def application(monkeypatch):
    project = Project(id=uuid4(), deleted_at=None)
    actor = Staff(
        id=uuid4(),
        project_id=project.id,
        role="admin",
        account_enabled=True,
        deleted_at=None,
    )
    db = Mock(spec=Session)

    def get(model, identifier, **kwargs):
        row = actor if model is Staff else project if model is Project else None
        return row if row is not None and row.id == identifier else None

    db.get.side_effect = get
    app = FastAPI()
    app.include_router(router, prefix="/internal/ai/activation")
    app.dependency_overrides[get_db] = lambda: db
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-activation-test-key")
    payload = {
        "project_id": str(project.id),
        "requested_by": str(actor.id),
        "template_id": "knowledge",
        "template_version": 1,
        "channel": "web",
    }
    return app, actor, project, db, payload


async def post(app, payload, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/internal/ai/activation/requirements",
            json=payload,
            headers=headers
            if headers is not None
            else {"X-Internal-API-Key": "synthetic-activation-test-key"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template", ["knowledge", "aftersales", "logistics", "assistant"]
)
async def test_owner_returns_all_requirements_without_authorizing_activation(
    application, template
):
    app, actor, project, db, payload = application
    payload["template_id"] = template
    response = await post(app, payload)
    assert response.status_code == 200
    value = AgentActivationRequirements.model_validate(response.json())
    assert value.project_id == project.id and value.requested_by == actor.id
    assert value.template_id == template and value.channel.value == "web"
    assert not value.can_enable and value.scope == "template_requirements"
    if template in {"knowledge", "aftersales"}:
        assert value.knowledge_cases == [
            row for row in list_cases() if row.template_id == template
        ]
        assert value.required_resources == ["model", "knowledge"]
        assert value.required_capabilities == ["governed_knowledge_search"]
    elif template == "logistics":
        assert value.required_resources == ["model", "connection"]
        assert value.required_capabilities == [
            "scoped_order_query",
            "scoped_logistics_query",
        ]
        assert value.knowledge_cases == []
    else:
        assert value.required_capabilities == ["business_data_access"]
    db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["role", "disabled", "deleted", "project", "project_deleted", "missing"]
)
async def test_current_admin_membership_is_required(application, state):
    app, actor, project, db, payload = application
    if state == "role":
        actor.role = "user"
    elif state == "disabled":
        actor.account_enabled = False
    elif state == "deleted":
        actor.deleted_at = datetime.now(UTC)
    elif state == "project":
        actor.project_id = uuid4()
    elif state == "project_deleted":
        project.deleted_at = datetime.now(UTC)
    else:
        payload["requested_by"] = str(uuid4())
    response = await post(app, payload)
    assert response.status_code == 403
    assert str(actor.id) not in response.text
    db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-Internal-API-Key": "wrong-service"}])
async def test_untrusted_service_cannot_read_requirements(application, headers):
    app, _, _, db, payload = application
    response = await post(app, payload, headers)
    assert response.status_code == 403
    db.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["cases", "resources", "enable", "bad_channel", "bool_version", "old_version"],
)
async def test_client_cannot_reduce_or_replace_requirements(application, change):
    app, _, _, db, payload = application
    if change == "cases":
        payload["knowledge_cases"] = []
    elif change == "resources":
        payload["required_resources"] = ["model"]
    elif change == "enable":
        payload["can_enable"] = True
    elif change == "bad_channel":
        payload["channel"] = "unverified"
    elif change == "bool_version":
        payload["template_version"] = True
    else:
        payload["template_version"] = 99
    response = await post(app, payload)
    assert response.status_code == (409 if change == "old_version" else 422)
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_revision_identifies_channel_and_current_case_definition(
    application, monkeypatch
):
    app, _, _, _, payload = application
    first = (await post(app, payload)).json()
    assert (await post(app, payload)).json()["requirements_revision"] == first[
        "requirements_revision"
    ]
    payload["channel"] = "wecom_kf"
    assert (await post(app, payload)).json()["requirements_revision"] != first[
        "requirements_revision"
    ]
    payload["channel"] = "web"
    template = agent_activation_requirements.get_template("agents", "knowledge", 1)
    template.test_questions[0] = "资料中更新后的材质是什么？"
    cases = list_cases()
    cases[0] = cases[0].model_copy(update={"question": template.test_questions[0]})
    monkeypatch.setattr(
        agent_activation_requirements, "get_template", lambda *args: template
    )
    monkeypatch.setattr(agent_activation_requirements, "list_cases", lambda: cases)
    revised = await post(app, payload)
    assert revised.status_code == 200
    assert revised.json()["requirements_revision"] != first["requirements_revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_owner_catalog_incomplete_or_out_of_sync_never_returns_requirements(
    application, monkeypatch, missing
):
    app, _, _, _, payload = application
    cases = list_cases()
    if missing:
        cases = []
    else:
        cases[0] = cases[0].model_copy(update={"question": "错误目录的问题"})
    monkeypatch.setattr(agent_activation_requirements, "list_cases", lambda: cases)
    assert (await post(app, payload)).status_code == 409
