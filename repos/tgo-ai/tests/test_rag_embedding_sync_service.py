"""Contract tests for project-scoped embedding configuration sync."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.services.rag_embedding_sync_service import (
    _map_provider_for_rag,
    build_embedding_configs,
)


def test_dashscope_provider_maps_to_qwen3() -> None:
    """The provider shape emitted by tgo-api must select the Qwen3 RAG client."""
    assert _map_provider_for_rag("openai_compatible", "dashscope") == "qwen3"


def test_other_openai_compatible_provider_keeps_generic_client() -> None:
    assert _map_provider_for_rag("openai_compatible", "deepseek") == "openai_compatible"


@pytest.mark.asyncio
async def test_dashscope_config_uses_1536_dimensions() -> None:
    project_id = uuid4()
    provider_id = uuid4()
    provider = SimpleNamespace(
        provider_kind="openai_compatible",
        vendor="dashscope",
        api_key="test-dashscope-key",
        api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        is_active=True,
    )
    result = Mock()
    result.scalar_one_or_none.return_value = provider
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    project_config = SimpleNamespace(
        project_id=project_id,
        default_embedding_provider_id=provider_id,
        default_embedding_model="qwen3.7-text-embedding",
    )

    configs = await build_embedding_configs(db, [project_config])

    assert len(configs) == 1
    config = configs[0]
    assert config.project_id == project_id
    assert config.provider == "qwen3"
    assert config.model == "qwen3.7-text-embedding"
    assert config.dimensions == 1536
    assert config.batch_size == 10
    assert config.api_key == "test-dashscope-key"
    assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
