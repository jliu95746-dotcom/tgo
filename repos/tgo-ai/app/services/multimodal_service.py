"""Provider-neutral orchestration for voice and image analysis."""

import asyncio
import logging
import math

from app.runtime.multimodal.providers.base import (
    MultimodalProviderRegistry,
    ProviderExecutionError,
)
from app.schemas.multimodal import (
    ASROutput,
    ASRRequest,
    AnalysisCapability,
    AnalysisErrorCategory,
    AnalysisStageError,
    AnalysisStageResult,
    AnalysisStageStatus,
    MediaAnalysisRequest,
    MediaAnalysisResult,
    MediaAnalysisStatus,
    MediaType,
    OCROutput,
    OCRRequest,
    ProviderSelection,
    SensitiveDataCategory,
    VLMOutput,
    VLMRequest,
)
from app.services.sensitive_data_redactor import SensitiveDataRedactor


logger = logging.getLogger(__name__)


class MultimodalService:
    """Compose ASR for voice and OCR plus VLM for images.

    Provider errors become typed terminal results. Incomplete analysis always
    fails closed: callers must show the fallback or hand off instead of
    silently continuing an automated response.
    """

    def __init__(
        self,
        registry: MultimodalProviderRegistry,
        selection: ProviderSelection,
        provider_timeout_seconds: float = 12.0,
        redactor: SensitiveDataRedactor | None = None,
    ) -> None:
        if (
            not math.isfinite(provider_timeout_seconds)
            or provider_timeout_seconds <= 0
        ):
            raise ValueError(
                "provider_timeout_seconds must be finite and positive"
            )
        self._registry = registry
        self._selection = selection
        self._provider_timeout_seconds = provider_timeout_seconds
        self._redactor = redactor or SensitiveDataRedactor()

    async def analyze(
        self, request: MediaAnalysisRequest
    ) -> MediaAnalysisResult:
        """Analyze media with the required provider combination."""
        if request.media_type is MediaType.VOICE:
            stage = await self._analyze_voice(request)
            return self._build_result(request, (stage,))

        ocr_task = asyncio.create_task(self._analyze_image_ocr(request))
        vlm_task = asyncio.create_task(self._analyze_image_vlm(request))
        ocr_stage, vlm_stage = await asyncio.gather(ocr_task, vlm_task)
        return self._build_result(request, (ocr_stage, vlm_stage))

    async def _analyze_voice(
        self, request: MediaAnalysisRequest
    ) -> AnalysisStageResult:
        provider_name = self._selection.asr
        if provider_name is None:
            return self._missing_provider_stage(AnalysisCapability.ASR, None)
        provider = self._registry.get_asr(provider_name)
        if provider is None:
            return self._missing_provider_stage(
                AnalysisCapability.ASR, provider_name
            )

        provider_request = ASRRequest(
            media_id=request.media_id,
            media_uri=request.media_uri,
            mime_type=request.mime_type,
            sha256=request.sha256,
            language=request.language,
        )
        try:
            raw_output = await asyncio.wait_for(
                provider.transcribe(provider_request),
                timeout=self._provider_timeout_seconds,
            )
            output = ASROutput.model_validate(raw_output)
            redacted = self._redactor.redact(output.transcript)
            return AnalysisStageResult(
                capability=AnalysisCapability.ASR,
                status=AnalysisStageStatus.COMPLETED,
                provider_name=provider_name,
                text=redacted.text,
                confidence=output.confidence,
                model_version=output.model_version,
                text_is_untrusted=True,
                sensitive_data_categories=redacted.categories,
            )
        except asyncio.TimeoutError:
            return self._timeout_stage(AnalysisCapability.ASR, provider_name)
        except ProviderExecutionError as exc:
            return self._provider_error_stage(
                AnalysisCapability.ASR, provider_name, exc
            )
        except Exception:
            logger.error(
                "ASR provider returned an invalid or failed response: "
                "provider=%s",
                provider_name,
            )
            return self._unexpected_error_stage(
                AnalysisCapability.ASR, provider_name
            )

    async def _analyze_image_ocr(
        self, request: MediaAnalysisRequest
    ) -> AnalysisStageResult:
        provider_name = self._selection.ocr
        if provider_name is None:
            return self._missing_provider_stage(AnalysisCapability.OCR, None)
        provider = self._registry.get_ocr(provider_name)
        if provider is None:
            return self._missing_provider_stage(
                AnalysisCapability.OCR, provider_name
            )

        provider_request = OCRRequest(
            media_id=request.media_id,
            media_uri=request.media_uri,
            mime_type=request.mime_type,
            sha256=request.sha256,
        )
        try:
            raw_output = await asyncio.wait_for(
                provider.extract_text(provider_request),
                timeout=self._provider_timeout_seconds,
            )
            output = OCROutput.model_validate(raw_output)
            redacted = self._redactor.redact(output.text)
            return AnalysisStageResult(
                capability=AnalysisCapability.OCR,
                status=AnalysisStageStatus.COMPLETED,
                provider_name=provider_name,
                text=redacted.text,
                confidence=output.confidence,
                model_version=output.model_version,
                text_is_untrusted=True,
                sensitive_data_categories=redacted.categories,
            )
        except asyncio.TimeoutError:
            return self._timeout_stage(AnalysisCapability.OCR, provider_name)
        except ProviderExecutionError as exc:
            return self._provider_error_stage(
                AnalysisCapability.OCR, provider_name, exc
            )
        except Exception:
            logger.error(
                "OCR provider returned an invalid or failed response: "
                "provider=%s",
                provider_name,
            )
            return self._unexpected_error_stage(
                AnalysisCapability.OCR, provider_name
            )

    async def _analyze_image_vlm(
        self, request: MediaAnalysisRequest
    ) -> AnalysisStageResult:
        provider_name = self._selection.vlm
        if provider_name is None:
            return self._missing_provider_stage(AnalysisCapability.VLM, None)
        provider = self._registry.get_vlm(provider_name)
        if provider is None:
            return self._missing_provider_stage(
                AnalysisCapability.VLM, provider_name
            )

        provider_request = VLMRequest(
            media_id=request.media_id,
            media_uri=request.media_uri,
            mime_type=request.mime_type,
            sha256=request.sha256,
        )
        try:
            raw_output = await asyncio.wait_for(
                provider.describe(provider_request),
                timeout=self._provider_timeout_seconds,
            )
            output = VLMOutput.model_validate(raw_output)
            redacted = self._redactor.redact(output.summary)
            return AnalysisStageResult(
                capability=AnalysisCapability.VLM,
                status=AnalysisStageStatus.COMPLETED,
                provider_name=provider_name,
                text=redacted.text,
                confidence=output.confidence,
                model_version=output.model_version,
                text_is_untrusted=True,
                sensitive_data_categories=redacted.categories,
            )
        except asyncio.TimeoutError:
            return self._timeout_stage(AnalysisCapability.VLM, provider_name)
        except ProviderExecutionError as exc:
            return self._provider_error_stage(
                AnalysisCapability.VLM, provider_name, exc
            )
        except Exception:
            logger.error(
                "VLM provider returned an invalid or failed response: "
                "provider=%s",
                provider_name,
            )
            return self._unexpected_error_stage(
                AnalysisCapability.VLM, provider_name
            )

    @staticmethod
    def _missing_provider_stage(
        capability: AnalysisCapability, provider_name: str | None
    ) -> AnalysisStageResult:
        return AnalysisStageResult(
            capability=capability,
            status=AnalysisStageStatus.FAILED,
            provider_name=provider_name,
            error=AnalysisStageError(
                category=AnalysisErrorCategory.PROVIDER_NOT_CONFIGURED,
                message=f"未配置可用的 {capability.value.upper()} Provider",
                retryable=False,
            ),
        )

    @staticmethod
    def _timeout_stage(
        capability: AnalysisCapability, provider_name: str
    ) -> AnalysisStageResult:
        return AnalysisStageResult(
            capability=capability,
            status=AnalysisStageStatus.FAILED,
            provider_name=provider_name,
            error=AnalysisStageError(
                category=AnalysisErrorCategory.TIMEOUT,
                message=f"{capability.value.upper()} Provider 处理超时",
                retryable=True,
            ),
        )

    @staticmethod
    def _provider_error_stage(
        capability: AnalysisCapability,
        provider_name: str,
        exc: ProviderExecutionError,
    ) -> AnalysisStageResult:
        if exc.category is AnalysisErrorCategory.INVALID_MEDIA:
            message = (
                "语音文件格式不受支持"
                if capability is AnalysisCapability.ASR
                else "图片文件格式不受支持"
            )
        else:
            message = f"{capability.value.upper()} Provider 处理失败"
        return AnalysisStageResult(
            capability=capability,
            status=AnalysisStageStatus.FAILED,
            provider_name=provider_name,
            error=AnalysisStageError(
                category=exc.category,
                message=message,
                retryable=exc.retryable,
            ),
        )

    @staticmethod
    def _unexpected_error_stage(
        capability: AnalysisCapability, provider_name: str
    ) -> AnalysisStageResult:
        return AnalysisStageResult(
            capability=capability,
            status=AnalysisStageStatus.FAILED,
            provider_name=provider_name,
            error=AnalysisStageError(
                category=AnalysisErrorCategory.PROVIDER_FAILURE,
                message=f"{capability.value.upper()} Provider 处理失败",
                retryable=True,
            ),
        )

    def _build_result(
        self,
        request: MediaAnalysisRequest,
        stages: tuple[AnalysisStageResult, ...],
    ) -> MediaAnalysisResult:
        completed_count = sum(
            stage.status is AnalysisStageStatus.COMPLETED for stage in stages
        )
        if completed_count == len(stages):
            status = MediaAnalysisStatus.COMPLETED
        elif completed_count == 0:
            status = MediaAnalysisStatus.FAILED
        else:
            status = MediaAnalysisStatus.PARTIAL

        normalized_parts: list[str] = []
        sensitive_categories: list[SensitiveDataCategory] = []
        if request.source_text is not None:
            redacted_source = self._redactor.redact(request.source_text)
            normalized_parts.append(f"用户附言：{redacted_source.text}")
            sensitive_categories.extend(redacted_source.categories)
        for stage in stages:
            sensitive_categories.extend(stage.sensitive_data_categories)
            if (
                stage.status is not AnalysisStageStatus.COMPLETED
                or stage.text is None
            ):
                continue
            if stage.capability is AnalysisCapability.ASR:
                normalized_parts.append(stage.text)
            elif stage.capability is AnalysisCapability.OCR:
                normalized_parts.append(f"OCR 文字：{stage.text}")
            else:
                normalized_parts.append(f"图片摘要：{stage.text}")

        if status is MediaAnalysisStatus.COMPLETED:
            fallback_message = None
            can_continue = True
            requires_handoff = False
        else:
            can_continue = False
            requires_handoff = True
            if request.media_type is MediaType.VOICE:
                fallback_message = "语音识别失败，请重新发送语音或转人工处理。"
            elif status is MediaAnalysisStatus.PARTIAL:
                fallback_message = "图片识别结果不完整，已转人工进一步处理。"
            else:
                fallback_message = "图片识别失败，请重新发送图片或转人工处理。"

        return MediaAnalysisResult(
            media_id=request.media_id,
            media_type=request.media_type,
            status=status,
            normalized_text=(
                "\n".join(normalized_parts) if normalized_parts else None
            ),
            stages=stages,
            can_continue=can_continue,
            requires_handoff=requires_handoff,
            fallback_message=fallback_message,
            normalized_text_is_untrusted=bool(normalized_parts),
            sensitive_data_categories=tuple(
                dict.fromkeys(sensitive_categories)
            ),
        )
