"""Run one selected model without changing project defaults or storing media."""
import hashlib
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_ai_config import ProjectAIConfig
from app.runtime.multimodal.providers.base import ProviderExecutionError
from app.runtime.multimodal.providers.http_provider import (
    HTTPMediaProvider,
    validate_media_content,
)
from app.schemas.media_probe import MediaProbeResult
from app.schemas.multimodal import (
    AnalysisCapability,
    ASRRequest,
    OCRRequest,
    VLMRequest,
    MediaAnalysisRequest,
    MediaType,
)
from app.services.configured_multimodal import ConfiguredMultimodalService

MAX_PROBE_BYTES = 5 * 1024 * 1024


async def probe_media_model(
    db: AsyncSession,
    project_id: uuid.UUID,
    provider_id: uuid.UUID,
    model_id: str,
    capability: AnalysisCapability,
    content: bytes,
    mime_type: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MediaProbeResult:
    try:
        request = MediaAnalysisRequest(
            media_id=uuid.uuid4().hex,
            media_uri="tgo-media://model-test/sample",
            media_type=MediaType.VOICE
            if capability is AnalysisCapability.ASR
            else MediaType.IMAGE,
            mime_type=mime_type,
            sha256=hashlib.sha256(content).hexdigest(),
        )
        media = validate_media_content(request, content, max_bytes=MAX_PROBE_BYTES)
        # Transient selection only: never attach to the session or save defaults.
        selection = ProjectAIConfig(project_id=project_id)
        setattr(selection, f"default_{capability.value}_provider_id", provider_id)
        setattr(selection, f"default_{capability.value}_model", model_id)
        model = await ConfiguredMultimodalService(db)._model(
            project_id, capability, selection
        )
        if model is None:
            return MediaProbeResult(
                success=False,
                error_code="configuration",
                message="所选模型不可用，请检查模型类型、启用状态、密钥及配置同步状态。",
            )
        values = dict(
            media_id=request.media_id,
            media_uri=request.media_uri,
            mime_type=request.mime_type,
            sha256=request.sha256,
        )
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            provider = HTTPMediaProvider(model, media, client)
            if capability is AnalysisCapability.ASR:
                output = (await provider.transcribe(ASRRequest(**values))).transcript
            elif capability is AnalysisCapability.OCR:
                output = (await provider.extract_text(OCRRequest(**values))).text
            else:
                output = (await provider.describe(VLMRequest(**values))).summary
        return MediaProbeResult(
            success=True, message="调用成功，请核对识别内容是否准确。", output=output
        )
    except ProviderExecutionError as exc:
        return MediaProbeResult(
            success=False, message=exc.public_message, error_code=exc.diagnostic_code
        )
    except ValueError:
        return MediaProbeResult(
            success=False, message="文件类型与所选模型不匹配。", error_code="invalid_media"
        )
