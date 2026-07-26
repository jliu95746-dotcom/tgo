"""Runtime defaults for latency-oriented DeepSeek customer-service models."""

from unittest.mock import MagicMock, patch

from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.models import AgentConfig, LLMProviderCredentials


def _deepseek_config(*, thinking_enabled: bool | None = None) -> AgentConfig:
    return AgentConfig(
        model_name="deepseek-v4-flash",
        thinking_enabled=thinking_enabled,
        provider_credentials=LLMProviderCredentials(
            provider_kind="openai_compatible",
            vendor="deepseek",
            api_key="test-key",
            api_base_url="https://api.deepseek.com",
        ),
    )


def test_deepseek_v4_flash_defaults_to_non_thinking_mode() -> None:
    builder = AgentBuilder.__new__(AgentBuilder)

    with patch("app.runtime.tools.builder.agent_builder.OpenAIChat") as openai_chat:
        builder._initialize_model(_deepseek_config())

    assert openai_chat.call_args.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_deepseek_v4_flash_can_explicitly_enable_thinking() -> None:
    builder = AgentBuilder.__new__(AgentBuilder)

    with patch("app.runtime.tools.builder.agent_builder.OpenAIChat") as openai_chat:
        builder._initialize_model(_deepseek_config(thinking_enabled=True))

    assert openai_chat.call_args.kwargs["extra_body"] == {
        "thinking": {"type": "enabled"}
    }


def test_text_ui_mode_does_not_inject_json_render_protocol() -> None:
    builder = AgentBuilder.__new__(AgentBuilder)
    builder._logger = MagicMock()

    with patch(
        "app.runtime.tools.builder.agent_builder.JsonRenderSchemaManager"
    ) as schema_manager:
        prompt = builder._compose_system_prompt("customer prompt", ui_mode="text")

    schema_manager.assert_not_called()
    assert prompt.startswith("customer prompt")
    assert "update_user_info" in prompt
    assert "update_user_sentiment" in prompt
    assert "add_user_tags" in prompt
    assert "update_user_memory" in prompt
    assert "passwords" in prompt


def test_json_render_ui_mode_keeps_structured_protocol() -> None:
    builder = AgentBuilder.__new__(AgentBuilder)
    builder._logger = MagicMock()

    with patch(
        "app.runtime.tools.builder.agent_builder.JsonRenderSchemaManager"
    ) as schema_manager:
        schema_manager.return_value.generate_system_prompt.return_value = (
            "json-render-protocol"
        )
        prompt = builder._compose_system_prompt(
            "structured prompt",
            ui_mode="json_render",
        )

    assert "json-render-protocol" in prompt
