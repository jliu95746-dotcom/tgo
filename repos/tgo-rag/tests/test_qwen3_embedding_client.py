"""Unit tests for DashScope's OpenAI-compatible embedding client."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.rag_service.services import embedding as embedding_module


@pytest.mark.asyncio
async def test_qwen3_client_requests_configured_dimensions(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeEmbeddingsAPI:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.25] * 1536)],
            )

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.embeddings = FakeEmbeddingsAPI()

    monkeypatch.setattr(embedding_module, "OpenAI", FakeOpenAI)
    client = embedding_module.Qwen3EmbeddingClient(
        api_key="test-dashscope-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-text-embedding",
        dimensions=1536,
        batch_size=10,
    )

    vector = await client.embed_query("企业微信的退换货规则是什么？")

    assert len(vector) == 1536
    assert captured["model"] == "qwen3.7-text-embedding"
    assert captured["input"] == ["企业微信的退换货规则是什么？"]
    assert captured["dimensions"] == 1536
    assert captured["encoding_format"] == "float"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["qwen3", "openai_compatible"])
@pytest.mark.parametrize("batch", [False, True])
async def test_embedding_chain_does_not_log_provider_secrets(
    monkeypatch, provider, batch,
) -> None:
    logger = Mock()

    class FailingOpenAI:
        def __init__(self, **kwargs):
            self.embeddings = SimpleNamespace(create=Mock(side_effect=RuntimeError(
                "HTTP 401 api_key=fixture-private-secret",
            )))

    monkeypatch.setattr(embedding_module, "OpenAI", FailingOpenAI)
    monkeypatch.setattr(embedding_module, "logger", logger)
    service = embedding_module.EmbeddingService(
        provider=provider, model="fixture-model", dimensions=1536,
        api_key="fixture-key",
        base_url="https://example.invalid/v1?token=fixture-private-secret",
    )
    with pytest.raises(Exception, match="HTTP 401"):
        if batch:
            await service.generate_embeddings_batch(["测试文档"])
        else:
            await service.generate_embedding("测试问题")
    assert logger.error.call_count >= 2
    assert "fixture-private-secret" not in repr(logger.mock_calls)
    assert all(call.kwargs.get("error_type") for call in logger.error.call_args_list)
