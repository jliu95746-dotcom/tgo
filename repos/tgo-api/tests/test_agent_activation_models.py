"""The model owner verifies current runtime settings without releasing secrets."""

from uuid import uuid4
from unittest.mock import Mock

import httpx
import pytest

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.project_ai_config import ProjectAIConfig
from app.schemas.platform_models import PlatformModelRuntime
from app.schemas.agent_activation_models import AgentActivationModel
from app.services import agent_activation_models
from app.utils.crypto import encrypt_str
from tests.test_agent_activation_requirements import application as application


@pytest.fixture
def model_application(application, monkeypatch):
    app, actor, project, db, _ = application
    provider = AIProvider(id=uuid4(), project_id=project.id, provider="openai",
        name="测试模型", api_key=encrypt_str("fixture-secret-model-key"), is_active=True,
        sync_status="synced", deleted_at=None, config={}, models=[])
    model = AIModel(id=uuid4(), provider_id=provider.id, model_id="fixture-model",
        model_name="隔离模型", model_type="chat", is_active=True, deleted_at=None)
    original = db.get.side_effect
    db.get.side_effect = lambda kind, identifier, **kw: (
        provider if kind is AIProvider and identifier == provider.id else original(kind, identifier, **kw)
    )
    db.scalar.return_value = model
    monkeypatch.setattr(agent_activation_models.settings, "SAAS_ENABLED", False)
    command = {"project_id": str(project.id), "requested_by": str(actor.id),
        "model": "fixture-model", "provider_id": str(provider.id)}
    return app, actor, db, provider, model, command


async def post(app, payload, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        return await client.post("/internal/ai/activation/model", json=payload,
            headers={"X-Internal-API-Key": "synthetic-activation-test-key"} if headers is None else headers)


@pytest.mark.asyncio
async def test_ready_model_has_private_revision_and_no_credential_payload(model_application):
    app, _, db, _, _, command = model_application
    response = await post(app, command)
    assert response.status_code == 200
    value = AgentActivationModel.model_validate(response.json())
    assert value.status == "ready" and value.effective_model == "fixture-model"
    assert value.runtime_revision and value.owner_revision and value.can_enable is False
    assert "fixture-secret-model-key" not in response.text and "api_key" not in response.text
    db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["key", "url", "model_metadata"])
async def test_owner_changes_invalidate_the_original_revision(model_application, changed):
    app, _, _, provider, model, command = model_application
    first = AgentActivationModel.model_validate((await post(app, command)).json())
    if changed == "key":
        provider.api_key = encrypt_str("another-fixture-key")
    elif changed == "url":
        provider.api_base_url = "https://changed.example.invalid/v1"
    else:
        model.capabilities = {"supports_images": True}
    second = AgentActivationModel.model_validate((await post(app, command)).json())
    assert first.owner_revision != second.owner_revision
    if changed != "model_metadata":
        assert first.runtime_revision != second.runtime_revision


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "sync", "foreign", "deleted", "model_disabled", "no_key"])
async def test_unready_owner_cannot_provide_a_ready_receipt(model_application, state):
    app, _, _, provider, model, command = model_application
    if state == "disabled":
        provider.is_active = False
    elif state == "sync":
        provider.sync_status = "pending"
    elif state == "foreign":
        provider.project_id = uuid4()
    elif state == "deleted":
        provider.deleted_at = "deleted"
    elif state == "model_disabled":
        model.is_active = False
    else:
        provider.api_key = ""
    value = AgentActivationModel.model_validate((await post(app, command)).json())
    assert value.status != "ready" and value.runtime_revision is None


@pytest.mark.asyncio
async def test_fresh_role_and_private_service_are_both_required(model_application):
    app, actor, db, _, _, command = model_application
    assert (await post(app, command, {})).status_code == 403
    actor.role = "user"
    assert (await post(app, command)).status_code == 403
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_implicit_model_without_an_owner_stays_missing(model_application):
    app, _, db, _, _, command = model_application
    db.scalar.return_value = None
    response = await post(app, {**command, "provider_id": None, "model": "__system_default__"})
    assert response.status_code == 200 and response.json()["status"] == "missing"


@pytest.mark.asyncio
async def test_system_default_uses_synced_current_configuration(model_application):
    app, _, db, provider, model, command = model_application
    config = ProjectAIConfig(project_id=provider.project_id,
        default_chat_provider_id=provider.id, default_chat_model=model.model_id, sync_status="synced")
    db.scalar.side_effect = [config, model]
    response = await post(app, {**command, "provider_id": None, "model": "__system_default__"})
    assert response.status_code == 200 and response.json()["status"] == "ready"
    config.sync_status = "pending"
    db.scalar.side_effect = [config]
    assert (await post(app, {**command, "model": "__system_default__"})).json()["status"] == "not_synced"


@pytest.mark.asyncio
async def test_saas_model_policy_is_current_and_never_falls_back_to_tenant(model_application, monkeypatch):
    app, _, _, _, _, command = model_application
    monkeypatch.setattr(agent_activation_models.settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(agent_activation_models.settings, "SAAS_BILLING_ENABLED", True)
    runtime = PlatformModelRuntime(model="platform-fixture", provider_kind="openai",
        api_key="private-platform-fixture-key")
    monkeypatch.setattr(agent_activation_models, "runtime_model", lambda _: runtime)
    policy = Mock(version=1)
    monkeypatch.setattr(agent_activation_models, "read_model_policy", lambda _: policy)
    selected = {**command, "provider_id": None, "model": "__system_default__"}
    first = (await post(app, selected)).json()
    assert first["status"] == "ready" and first["source"] == "platform"
    assert "private-platform-fixture-key" not in str(first)
    policy.version = 2
    assert (await post(app, selected)).json()["owner_revision"] != first["owner_revision"]
    monkeypatch.setattr(agent_activation_models, "runtime_model", lambda _: None)
    assert (await post(app, command)).json()["status"] == "missing"


@pytest.mark.asyncio
async def test_rejected_model_query_does_not_echo_private_fields(model_application):
    app, _, _, _, _, command = model_application
    response = await post(app, {**command, "api_key": "rejected-private-model-marker"})
    assert response.status_code == 422 and "rejected-private-model-marker" not in response.text
