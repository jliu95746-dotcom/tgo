"""Platform ceilings cannot be increased through enterprise agent settings."""

import pytest
from pydantic import SecretStr

from app.config import settings
from app.runtime.tools.models import AgentConfig
from app.services.commercial_ai_limits import constrain_agent
from app.services.quota_authorization import metered_execution
from app.services.service_identity import rag_service_headers


@pytest.fixture
def commercial(monkeypatch):
    token = metered_execution.set(True)
    monkeypatch.setattr(settings, 'saas_approved_models', ['synthetic-model'])
    try:
        yield
    finally:
        metered_execution.reset(token)


def test_customer_values_cannot_increase_platform_ceilings(commercial):
    original = AgentConfig(model_name='synthetic-model', max_tokens=100000, tool_call_limit=100000, num_history_runs=100000)
    bounded = constrain_agent(original, 'hello')
    assert bounded.max_tokens == settings.saas_max_output_tokens
    assert bounded.tool_call_limit == settings.saas_max_tool_calls
    assert bounded.num_history_runs == settings.saas_max_history_runs
    assert original.max_tokens == 100000


def test_unapproved_model_and_oversized_input_are_rejected(commercial):
    with pytest.raises(ValueError):
        constrain_agent(AgentConfig(model_name='unapproved'), 'hello')
    with pytest.raises(ValueError):
        constrain_agent(AgentConfig(model_name='synthetic-model'), 'x' * (settings.saas_max_input_characters + 1))


def test_legacy_execution_keeps_existing_configuration():
    token = metered_execution.set(False)
    try:
        original = AgentConfig(model_name='legacy')
        assert constrain_agent(original, 'hello') is original
    finally:
        metered_execution.reset(token)


def test_internal_token_cannot_be_sent_to_custom_rag_url(monkeypatch):
    monkeypatch.setattr(settings, 'saas_enabled', True)
    monkeypatch.setattr(settings, 'saas_billing_enabled', True)
    monkeypatch.setattr(settings, 'saas_internal_token', SecretStr('synthetic-test-only'))
    with pytest.raises(ValueError):
        rag_service_headers('https://untrusted.example')
    assert rag_service_headers(settings.rag_service_url)['X-SaaS-Service-Token'] == 'synthetic-test-only'
