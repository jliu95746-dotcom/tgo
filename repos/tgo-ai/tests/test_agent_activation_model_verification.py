"""Runtime and readable report revisions must match the current owner."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.exceptions import ConflictError
from app.models.agent import Agent as StoredAgent
from app.runtime.tools.models import LLMProviderCredentials
from app.schemas.agent_activation_models import ActivationModelRuntime, AgentActivationModel
from app.services.agent_activation_model_verification import verify_model_configuration
from app.services.agent_trial_fingerprint import configuration_fingerprint
from tests.test_agent_knowledge_trial import employee


def fixture():
    agent, actor = employee(), uuid4()
    stored = StoredAgent(id=agent.id, project_id=UUID(agent.project_id), model=agent.model,
        llm_provider_id=uuid4(), name=agent.name)
    agent.llm_provider_credentials = LLMProviderCredentials(provider_kind="openai", api_key="fixture-private-key")
    revision = ActivationModelRuntime(model=agent.model, provider_kind="openai",
        api_key="fixture-private-key").private_revision("fixture-owner-key")
    owner = AsyncMock()
    owner.get_agent_activation_model.return_value = AgentActivationModel(
        project_id=stored.project_id, requested_by=actor, model=stored.model,
        provider_id=stored.llm_provider_id, status="ready", effective_model=agent.model,
        runtime_revision=revision, owner_revision="b" * 64)
    return stored, agent, actor, owner


@pytest.mark.asyncio
async def test_verified_identity_changes_when_the_owner_revision_changes(monkeypatch):
    monkeypatch.setattr("app.services.agent_activation_model_verification.settings.secret_key", "fixture-owner-key")
    stored, agent, actor, owner = fixture()
    first = await verify_model_configuration(stored, agent, actor, owner)
    assert first.model_owner_revision == "b" * 64
    owner.get_agent_activation_model.return_value.owner_revision = "c" * 64
    second = await verify_model_configuration(stored, agent, actor, owner)
    assert configuration_fingerprint(first) != configuration_fingerprint(second)
    assert agent.model_owner_revision is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["model", "runtime", "unavailable", "project", "actor", "provider"])
async def test_stale_runtime_or_wrong_owner_cannot_enter_a_trial(monkeypatch, fault):
    monkeypatch.setattr("app.services.agent_activation_model_verification.settings.secret_key", "fixture-owner-key")
    stored, agent, actor, owner = fixture()
    proof = owner.get_agent_activation_model.return_value
    if fault == "model":
        proof.effective_model = "another-model"
    elif fault == "runtime":
        proof.runtime_revision = "a" * 64
    elif fault == "unavailable":
        proof.status, proof.runtime_revision, proof.owner_revision = "unavailable", None, None
    else:
        setattr(proof, {"project": "project_id", "actor": "requested_by", "provider": "provider_id"}[fault], uuid4())
    with pytest.raises(ConflictError):
        await verify_model_configuration(stored, agent, actor, owner)


@pytest.mark.asyncio
async def test_platform_report_read_needs_no_plain_credentials(monkeypatch):
    stored, agent, actor, owner = fixture()
    proof = owner.get_agent_activation_model.return_value
    proof.source = "platform"
    stored.llm_provider_id, proof.provider_id = None, None
    agent.llm_provider_credentials = None
    value = await verify_model_configuration(stored, agent, actor, owner, verify_runtime=False)
    assert value.model_owner_revision == proof.owner_revision and value.llm_provider_credentials is None
    with pytest.raises(ConflictError):
        await verify_model_configuration(stored, agent, actor, owner)


def test_default_switch_does_not_invalidate_explicit_knowledge_configuration():
    _, agent, _, _ = fixture()
    assert configuration_fingerprint(agent) == configuration_fingerprint(agent.model_copy(update={"is_default": True}))
