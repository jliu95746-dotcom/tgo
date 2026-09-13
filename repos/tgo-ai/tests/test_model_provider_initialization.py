"""Validate the installed model SDK constructors without contacting providers."""
from unittest.mock import Mock

import pytest

from agno.models.anthropic import Claude
from agno.models.google import Gemini
from agno.models.openai import OpenAIChat
from app.runtime.core.exceptions import InvalidConfigurationError
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import AgentConfig, LLMProviderCredentials


def configuration(kind, **overrides):
    return AgentConfig(model_name="fixture-model", temperature=0.3, max_tokens=512,
                       provider_credentials=LLMProviderCredentials(
                           provider_kind=kind, api_key="fixture-only-key",
                           api_base_url="https://provider.invalid/custom", timeout=2.5), **overrides)


def builder():
    return AgentBuilder(ToolsRuntimeSettings())


def test_google_uses_generation_limit_and_millisecond_http_options(monkeypatch):
    client = Mock()
    monkeypatch.setattr("agno.models.google.gemini.genai.Client", client)
    model = builder()._initialize_model(configuration("google"))
    assert isinstance(model, Gemini)
    assert model.max_output_tokens == 512 and model.temperature == 0.3
    model.get_client()
    kwargs = client.call_args.kwargs
    assert kwargs["api_key"] == "fixture-only-key"
    options = kwargs["http_options"]
    assert options.base_url == "https://provider.invalid/custom"
    assert options.timeout == 2500
    assert "base_url" not in kwargs and "timeout" not in kwargs


def test_anthropic_keeps_saved_custom_endpoint_and_request_options():
    model = builder()._initialize_model(configuration("anthropic"))
    assert isinstance(model, Claude)
    assert model.max_tokens == 512 and model.temperature == 0.3
    assert model._get_client_params()["base_url"] == "https://provider.invalid/custom"
    assert model._get_client_params()["timeout"] == 2.5


@pytest.mark.parametrize("kind", ["openai", "openai_compatible"])
def test_openai_compatible_settings_remain_unchanged(kind):
    config = configuration(kind)
    config.provider_credentials.organization = "fixture-org"
    model = builder()._initialize_model(config)
    assert isinstance(model, OpenAIChat)
    assert model.id == "fixture-model" and model.max_tokens == 512
    assert model.temperature == 0.3 and model.timeout == 2.5
    assert model.base_url == "https://provider.invalid/custom"
    assert model.organization == "fixture-org"
    assert model.role_map["system"] == "system"
    assert model.extra_body is None


@pytest.mark.parametrize("kind,model_class", [("openai", OpenAIChat), ("anthropic", Claude), ("google", Gemini)])
def test_unspecified_options_keep_sdk_defaults(kind, model_class):
    config = configuration(kind)
    config.max_tokens = None
    config.temperature = None
    config.provider_credentials.api_base_url = None
    config.provider_credentials.timeout = None
    model = builder()._initialize_model(config)
    reference = model_class(id="fixture-model", api_key="fixture-only-key")
    limit_field = "max_output_tokens" if kind == "google" else "max_tokens"
    assert getattr(model, limit_field) == getattr(reference, limit_field)
    assert model.temperature is None
    assert model.client_params is None


def test_constructor_error_does_not_expose_credentials(monkeypatch):
    monkeypatch.setattr("app.runtime.tools.builder.agent_builder.OpenAIChat", Mock(
        side_effect=ValueError("invalid credential: fixture-only-key")))
    with pytest.raises(InvalidConfigurationError) as error:
        builder()._initialize_model(configuration("openai"))
    assert "fixture-only-key" not in str(error.value)
    assert error.value.context["error_type"] == "ValueError"
