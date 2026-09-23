"""Paid enterprise runs use trusted platform credentials, never customer replacements."""

from unittest.mock import AsyncMock
from uuid import uuid4
import pytest

from app.runtime.tools.models import AgentConfig, LLMProviderCredentials
from app.schemas.chat import ChatCompletionRequest
from app.schemas.platform_models import PlatformModelRuntime
from app.services.commercial_ai_limits import constrain_agent, constrain_chat
from app.services.quota_authorization import current_platform_model, metered_execution
from app.services.system_default_model import resolve_system_default_model
from app.services.platform_models import provider_for_request
from tests.test_supervisor_agent_runner import _build_context


@pytest.fixture
def platform():
    profile = PlatformModelRuntime(
        model="synthetic-approved",
        provider_kind="openai",
        api_key="synthetic-platform-key",
    )
    profile_token = current_platform_model.set(profile)
    meter_token = metered_execution.set(True)
    try:
        yield profile
    finally:
        current_platform_model.reset(profile_token)
        metered_execution.reset(meter_token)


@pytest.mark.asyncio
async def test_new_company_default_resolves_without_copying_credentials_to_database(
    platform,
):
    agent = _build_context().agent.model_copy(update={"model": "__system_default__"})
    db = AsyncMock()
    result = await resolve_system_default_model(db, agent)
    assert result.model == platform.model
    assert result.llm_provider_credentials.api_key == "synthetic-platform-key"
    db.execute.assert_not_called()
    assert agent.model == "__system_default__"


def test_customer_cannot_override_approved_provider_or_model(platform):
    supplied = AgentConfig(
        model_name=platform.model,
        provider_credentials=LLMProviderCredentials(
            provider_kind="openai",
            api_key="synthetic-customer-key",
            api_base_url="https://unapproved.invalid",
        ),
    )
    bounded = constrain_agent(supplied, "hello")
    assert bounded.provider_credentials.api_key == "synthetic-platform-key"
    assert bounded.provider_credentials.api_base_url is None
    with pytest.raises(ValueError):
        constrain_agent(supplied.model_copy(update={"model_name": "other"}), "hello")
    project = uuid4()
    provider = provider_for_request(uuid4(), project)
    assert (
        provider.project_id == project and provider.api_key == "synthetic-platform-key"
    )
    request = ChatCompletionRequest(
        provider_id=uuid4(),
        model="__system_default__",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert constrain_chat(request).model == platform.model
