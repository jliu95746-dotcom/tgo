"""Contracts for multimodal defaults synchronized from tgo-api."""

from uuid import uuid4

from app.schemas.project_ai_config import ProjectAIConfigUpsert


def test_project_ai_config_schema_keeps_multimodal_defaults() -> None:
    provider_id = uuid4()

    config = ProjectAIConfigUpsert(
        project_id=uuid4(),
        default_asr_provider_id=provider_id,
        default_asr_model="speech-model",
        default_ocr_provider_id=provider_id,
        default_ocr_model="ocr-model",
        default_vlm_provider_id=provider_id,
        default_vlm_model="vision-model",
    )

    assert config.default_asr_provider_id == provider_id
    assert config.default_asr_model == "speech-model"
    assert config.default_ocr_provider_id == provider_id
    assert config.default_ocr_model == "ocr-model"
    assert config.default_vlm_provider_id == provider_id
    assert config.default_vlm_model == "vision-model"
