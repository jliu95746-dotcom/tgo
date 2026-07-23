"""Unit tests for DashScope's OpenAI-compatible embedding client."""

from types import SimpleNamespace

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
        model="text-embedding-v4",
        dimensions=1536,
        batch_size=10,
    )

    vector = await client.embed_query("企业微信的退换货规则是什么？")

    assert len(vector) == 1536
    assert captured["model"] == "text-embedding-v4"
    assert captured["input"] == ["企业微信的退换货规则是什么？"]
    assert captured["dimensions"] == 1536
    assert captured["encoding_format"] == "float"
