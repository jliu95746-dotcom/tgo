"""Contract tests for DashScope provider synchronization to tgo-ai."""

from app.services.ai_provider_sync import _map_kind_and_vendor


def test_dashscope_is_synced_as_named_openai_compatible_vendor() -> None:
    assert _map_kind_and_vendor("dashscope") == (
        "openai_compatible",
        "dashscope",
    )
