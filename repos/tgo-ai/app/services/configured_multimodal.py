"""Bind existing project defaults to concrete, tenant-scoped media providers."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.llm_model import LLMModel
from app.models.llm_provider import LLMProvider
from app.models.project_ai_config import ProjectAIConfig
from app.runtime.multimodal.providers.base import MultimodalProviderRegistry
from app.runtime.multimodal.providers.http_provider import (
    ConfiguredMediaModel,
    HTTPMediaProvider,
    validate_media_content,
)
from app.schemas.multimodal import (
    AnalysisCapability,
    MediaAnalysisRequest,
    MediaAnalysisResult,
    MediaType,
    ProviderSelection,
)
from app.services.multimodal_service import MultimodalService


class ConfiguredMultimodalService:
    def __init__(
        self, db: AsyncSession, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._db = db
        self._transport = transport

    async def _model(
        self,
        project_id: uuid.UUID,
        capability: AnalysisCapability,
        config: ProjectAIConfig | None,
    ) -> ConfiguredMediaModel | None:
        if config is None:
            return None
        provider_id: uuid.UUID | None = getattr(
            config, f"default_{capability.value}_provider_id"
        )
        model_name: str | None = getattr(config, f"default_{capability.value}_model")
        if provider_id is None or not model_name:
            return None
        from app.services.platform_models import current_model
        from app.services.quota_authorization import metered_execution
        platform = current_model()
        if metered_execution.get():
            approved = [platform.model] if platform else settings.saas_approved_models
            if model_name not in approved:
                return None
        row = (
            await self._db.execute(
                select(LLMProvider, LLMModel)
                .join(LLMModel, LLMModel.provider_id == LLMProvider.id)
                .where(
                    LLMProvider.id == provider_id,
                    LLMProvider.project_id == project_id,
                    LLMProvider.is_active.is_(True),
                    LLMModel.model_id == model_name,
                    LLMModel.is_active.is_(True),
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        provider, model = row
        if provider.provider_kind not in {"openai", "openai_compatible"}:
            return None
        supported = model.model_type == capability.value
        if capability in (AnalysisCapability.OCR, AnalysisCapability.VLM):
            supported = (
                supported
                or model.model_type == "vlm"
                or (
                    model.model_type == "chat"
                    and (model.capabilities or {}).get("vision") is True
                )
            )
        api_key = platform.api_key.get_secret_value() if platform else provider.api_key
        api_base_url = platform.api_base_url if platform else provider.api_base_url
        provider_kind = platform.provider_kind if platform else provider.provider_kind
        if provider_kind not in {"openai", "openai_compatible"}:
            return None
        if not supported or not api_key or not api_base_url:
            return None
        timeout = settings.multimodal_provider_timeout_seconds
        if provider.timeout is not None and provider.timeout > 0:
            timeout = min(timeout, provider.timeout)
        return ConfiguredMediaModel(
            name=str(provider.id),
            model=model_name,
            api_base_url=api_base_url,
            api_key=api_key,
            timeout_seconds=timeout,
            organization=None if platform else provider.organization,
        )

    async def analyze(
        self,
        project_id: uuid.UUID,
        request: MediaAnalysisRequest,
        content: bytes,
    ) -> MediaAnalysisResult:
        media = validate_media_content(
            request, content, max_bytes=settings.multimodal_max_media_bytes
        )
        config = await self._db.get(ProjectAIConfig, project_id)
        capabilities = (
            (AnalysisCapability.ASR,)
            if request.media_type is MediaType.VOICE
            else (
                AnalysisCapability.OCR,
                AnalysisCapability.VLM,
            )
        )
        # AsyncSession must never execute concurrent model lookups.
        models = {
            capability: await self._model(project_id, capability, config)
            for capability in capabilities
        }
        registry = MultimodalProviderRegistry()
        asr_name = ocr_name = vlm_name = None
        async with httpx.AsyncClient(
            transport=self._transport, trust_env=False
        ) as client:
            for capability, model in models.items():
                if model is None:
                    continue
                provider = HTTPMediaProvider(model, media, client)
                if capability is AnalysisCapability.ASR:
                    registry.register_asr(provider)
                    asr_name = provider.name
                elif capability is AnalysisCapability.OCR:
                    registry.register_ocr(provider)
                    ocr_name = provider.name
                else:
                    registry.register_vlm(provider)
                    vlm_name = provider.name
            return await MultimodalService(
                registry,
                ProviderSelection(asr=asr_name, ocr=ocr_name, vlm=vlm_name),
                provider_timeout_seconds=settings.multimodal_provider_timeout_seconds,
            ).analyze(request)
