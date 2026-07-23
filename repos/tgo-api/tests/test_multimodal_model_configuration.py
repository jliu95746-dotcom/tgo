"""Contracts for project-level multimodal model configuration."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.ai_provider import AIModelInput
from app.schemas.project_ai_config import ProjectAIConfigUpdate
from app.services.project_ai_config_sync import _config_to_upsert


@pytest.mark.parametrize("model_type", ["chat", "embedding", "asr", "ocr", "vlm"])
def test_provider_model_accepts_supported_model_types(model_type: str) -> None:
    model = AIModelInput(model_id="model-1", model_type=model_type)

    assert model.model_type == model_type


def test_provider_model_rejects_unknown_model_type() -> None:
    with pytest.raises(ValidationError):
        AIModelInput(model_id="model-1", model_type="unknown")


def test_project_config_accepts_multimodal_defaults() -> None:
    provider_id = uuid4()

    config = ProjectAIConfigUpdate(
        default_asr_provider_id=provider_id,
        default_asr_model="speech-model",
        default_ocr_provider_id=provider_id,
        default_ocr_model="ocr-model",
        default_vlm_provider_id=provider_id,
        default_vlm_model="vision-model",
    )

    assert config.default_asr_model == "speech-model"
    assert config.default_ocr_model == "ocr-model"
    assert config.default_vlm_model == "vision-model"


def test_project_config_sync_includes_multimodal_defaults() -> None:
    provider_id = uuid4()
    config = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        default_chat_provider_id=None,
        default_chat_model=None,
        default_embedding_provider_id=None,
        default_embedding_model=None,
        default_asr_provider_id=provider_id,
        default_asr_model="speech-model",
        default_ocr_provider_id=provider_id,
        default_ocr_model="ocr-model",
        default_vlm_provider_id=provider_id,
        default_vlm_model="vision-model",
    )

    payload = _config_to_upsert(config)

    assert payload["default_asr_provider_id"] == str(provider_id)
    assert payload["default_asr_model"] == "speech-model"
    assert payload["default_ocr_provider_id"] == str(provider_id)
    assert payload["default_ocr_model"] == "ocr-model"
    assert payload["default_vlm_provider_id"] == str(provider_id)
    assert payload["default_vlm_model"] == "vision-model"
