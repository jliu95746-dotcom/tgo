"""Media reports never fabricate token usage or use an unapproved provider."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import pytest

from app.models.project_ai_config import ProjectAIConfig
from app.schemas.multimodal import AnalysisCapability
from app.services.configured_multimodal import ConfiguredMultimodalService
from app.services.media_accounting import observe_media_usage
from app.services.model_usage import UsageTracker
from tests.test_platform_model_runtime import platform  # noqa: F401


def test_missing_media_usage_remains_unknown_and_actual_zero_is_preserved():
    tracker = UsageTracker()
    observe_media_usage(tracker, b'{"text":"synthetic"}')
    assert tracker.input_tokens is None and tracker.output_tokens is None
    observe_media_usage(tracker, b'{"usage":{"prompt_tokens":0,"completion_tokens":3}}')
    assert tracker.input_tokens == 0 and tracker.output_tokens == 3


@pytest.mark.asyncio
async def test_media_rejects_unapproved_model_before_loading_credentials(platform):
    project = uuid4()
    config = ProjectAIConfig(
        project_id=project,
        default_ocr_provider_id=uuid4(),
        default_ocr_model="unapproved",
    )
    db = AsyncMock()
    result = await ConfiguredMultimodalService(db)._model(
        project, AnalysisCapability.OCR, config
    )
    assert result is None
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_media_uses_platform_endpoint_and_key_when_model_is_approved(platform):
    project = uuid4()
    platform.api_base_url = "https://synthetic-platform.invalid/v1"
    config = ProjectAIConfig(
        project_id=project,
        default_ocr_provider_id=uuid4(),
        default_ocr_model=platform.model,
    )
    provider = SimpleNamespace(
        id=config.default_ocr_provider_id,
        provider_kind="openai",
        api_key="synthetic-customer-key",
        api_base_url="https://synthetic-customer.invalid",
        timeout=None,
        organization="customer-org",
    )
    model = SimpleNamespace(model_type="vlm")
    db = AsyncMock()
    db.execute.return_value = Mock(first=Mock(return_value=(provider, model)))
    result = await ConfiguredMultimodalService(db)._model(
        project, AnalysisCapability.OCR, config
    )
    assert result.api_key == platform.api_key.get_secret_value()
    assert result.api_base_url == platform.api_base_url
    assert result.organization is None
