"""Contract tests for provider-neutral multimodal analysis."""

import asyncio

import pytest
from pydantic import ValidationError

from app.runtime.multimodal.providers.base import (
    DuplicateProviderError,
    MultimodalProviderRegistry,
    ProviderExecutionError,
)
from app.schemas.multimodal import (
    ASROutput,
    ASRRequest,
    AnalysisCapability,
    AnalysisErrorCategory,
    AnalysisStageStatus,
    MediaAnalysisRequest,
    MediaAnalysisStatus,
    MediaType,
    OCROutput,
    OCRRequest,
    ProviderSelection,
    SensitiveDataCategory,
    VLMOutput,
    VLMRequest,
)
from app.services.multimodal_service import MultimodalService


SHA256 = "a" * 64


class FakeASRProvider:
    """ASR provider returning a deterministic transcript."""

    name = "fake-asr"

    async def transcribe(self, request: ASRRequest) -> ASROutput:
        assert request.sha256 == SHA256
        return ASROutput(
            transcript="我要查询订单",
            confidence=0.98,
            language="zh-CN",
            model_version="asr-test-v1",
        )


class FakeOCRProvider:
    """OCR provider returning deterministic extracted text."""

    name = "fake-ocr"

    async def extract_text(self, request: OCRRequest) -> OCROutput:
        assert request.mime_type == "image/png"
        return OCROutput(
            text="订单号 A1001",
            confidence=0.91,
            model_version="ocr-test-v1",
        )


class FakeVLMProvider:
    """VLM provider returning a deterministic summary."""

    name = "fake-vlm"

    async def describe(self, request: VLMRequest) -> VLMOutput:
        assert request.media_id == "image-1"
        return VLMOutput(
            summary="物流页面显示包裹仍在运输中",
            confidence=0.87,
            model_version="vlm-test-v1",
        )


class TimeoutOCRProvider:
    """OCR provider that exceeds the configured deadline."""

    name = "slow-ocr"

    async def extract_text(self, request: OCRRequest) -> OCROutput:
        await asyncio.sleep(0.05)
        return OCROutput(text="too late", model_version="ocr-slow-v1")


class FailedASRProvider:
    """ASR provider exposing a safe, classified provider error."""

    name = "failed-asr"

    async def transcribe(self, request: ASRRequest) -> ASROutput:
        raise ProviderExecutionError(
            public_message="语音文件格式不受支持",
            category=AnalysisErrorCategory.INVALID_MEDIA,
            retryable=False,
        )


class UnexpectedVLMProvider:
    """VLM provider raising an internal exception that must not leak."""

    name = "broken-vlm"

    async def describe(self, request: VLMRequest) -> VLMOutput:
        raise RuntimeError("secret vendor response")


class MalformedASRProvider:
    """Dynamic provider returning a value that violates its Protocol."""

    name = "malformed-asr"

    async def transcribe(self, request: ASRRequest) -> ASROutput:
        return {"transcript": 123}  # type: ignore[return-value]


def _voice_request() -> MediaAnalysisRequest:
    return MediaAnalysisRequest(
        media_id="voice-1",
        media_type=MediaType.VOICE,
        media_uri="s3://media/voice-1.amr",
        mime_type="audio/amr",
        sha256=SHA256,
        language="zh-CN",
    )


def _image_request() -> MediaAnalysisRequest:
    return MediaAnalysisRequest(
        media_id="image-1",
        media_type=MediaType.IMAGE,
        media_uri="s3://media/image-1.png",
        mime_type="image/png",
        sha256=SHA256,
        source_text="请帮我看物流",
    )


def _registry() -> MultimodalProviderRegistry:
    registry = MultimodalProviderRegistry()
    registry.register_asr(FakeASRProvider())
    registry.register_ocr(FakeOCRProvider())
    registry.register_vlm(FakeVLMProvider())
    return registry


@pytest.mark.asyncio
async def test_voice_uses_asr_and_returns_unified_result() -> None:
    service = MultimodalService(
        registry=_registry(),
        selection=ProviderSelection(asr="fake-asr"),
    )

    result = await service.analyze(_voice_request())

    assert result.status is MediaAnalysisStatus.COMPLETED
    assert result.normalized_text == "我要查询订单"
    assert result.can_continue is True
    assert result.requires_handoff is False
    assert len(result.stages) == 1
    assert result.stages[0].capability is AnalysisCapability.ASR
    assert result.stages[0].status is AnalysisStageStatus.COMPLETED


@pytest.mark.asyncio
async def test_image_combines_source_text_ocr_and_vlm() -> None:
    service = MultimodalService(
        registry=_registry(),
        selection=ProviderSelection(ocr="fake-ocr", vlm="fake-vlm"),
    )

    result = await service.analyze(_image_request())

    assert result.status is MediaAnalysisStatus.COMPLETED
    assert result.normalized_text == (
        "用户附言：请帮我看物流\n"
        "OCR 文字：订单号 A1001\n"
        "图片摘要：物流页面显示包裹仍在运输中"
    )
    assert result.can_continue is True
    assert [stage.capability for stage in result.stages] == [
        AnalysisCapability.OCR,
        AnalysisCapability.VLM,
    ]


@pytest.mark.asyncio
async def test_image_timeout_returns_partial_and_fails_closed() -> None:
    registry = MultimodalProviderRegistry()
    registry.register_ocr(TimeoutOCRProvider())
    registry.register_vlm(FakeVLMProvider())
    service = MultimodalService(
        registry=registry,
        selection=ProviderSelection(ocr="slow-ocr", vlm="fake-vlm"),
        provider_timeout_seconds=0.001,
    )

    result = await service.analyze(_image_request())

    assert result.status is MediaAnalysisStatus.PARTIAL
    assert result.can_continue is False
    assert result.requires_handoff is True
    assert result.fallback_message is not None
    ocr_stage = result.stages[0]
    assert ocr_stage.status is AnalysisStageStatus.FAILED
    assert ocr_stage.error is not None
    assert ocr_stage.error.category is AnalysisErrorCategory.TIMEOUT
    assert ocr_stage.error.retryable is True
    assert result.stages[1].status is AnalysisStageStatus.COMPLETED


@pytest.mark.asyncio
async def test_provider_failure_returns_visible_failed_result() -> None:
    registry = MultimodalProviderRegistry()
    registry.register_asr(FailedASRProvider())
    service = MultimodalService(
        registry=registry,
        selection=ProviderSelection(asr="failed-asr"),
    )

    result = await service.analyze(_voice_request())

    assert result.status is MediaAnalysisStatus.FAILED
    assert result.normalized_text is None
    assert result.can_continue is False
    assert result.requires_handoff is True
    assert (
        result.fallback_message == "语音识别失败，请重新发送语音或转人工处理。"
    )
    error = result.stages[0].error
    assert error is not None
    assert error.category is AnalysisErrorCategory.INVALID_MEDIA
    assert error.message == "语音文件格式不受支持"


@pytest.mark.asyncio
async def test_missing_provider_and_unexpected_error_are_classified() -> None:
    registry = MultimodalProviderRegistry()
    registry.register_vlm(UnexpectedVLMProvider())
    service = MultimodalService(
        registry=registry,
        selection=ProviderSelection(ocr="not-registered", vlm="broken-vlm"),
    )

    result = await service.analyze(_image_request())

    assert result.status is MediaAnalysisStatus.FAILED
    assert result.can_continue is False
    categories = [
        stage.error.category
        for stage in result.stages
        if stage.error is not None
    ]
    assert categories == [
        AnalysisErrorCategory.PROVIDER_NOT_CONFIGURED,
        AnalysisErrorCategory.PROVIDER_FAILURE,
    ]
    assert "secret vendor response" not in str(result)


def test_request_contract_rejects_mismatched_media_and_invalid_hash() -> None:
    with pytest.raises(ValidationError):
        MediaAnalysisRequest(
            media_id="bad-voice",
            media_type=MediaType.VOICE,
            media_uri="s3://media/file.png",
            mime_type="image/png",
            sha256="not-a-sha256",
        )

    with pytest.raises(ValidationError):
        MediaAnalysisRequest(
            media_id="unsafe-image",
            media_type=MediaType.IMAGE,
            media_uri="https://internal.example/media.png?token=secret",
            mime_type="image/png",
            sha256=SHA256,
        )


def test_registry_rejects_duplicate_provider_names() -> None:
    registry = MultimodalProviderRegistry()
    registry.register_asr(FakeASRProvider())

    with pytest.raises(DuplicateProviderError):
        registry.register_asr(FakeASRProvider())


@pytest.mark.asyncio
async def test_provider_and_source_text_sensitive_data_is_redacted() -> None:
    class SensitiveOCRProvider:
        name = "sensitive-ocr"

        async def extract_text(self, request: OCRRequest) -> OCROutput:
            return OCROutput(
                text=(
                    "手机号 13800138000，身份证 11010519491231002X，"
                    "银行卡号 6222021234567890"
                ),
                model_version="ocr-sensitive-v1",
            )

    registry = MultimodalProviderRegistry()
    registry.register_ocr(SensitiveOCRProvider())
    registry.register_vlm(FakeVLMProvider())
    service = MultimodalService(
        registry=registry,
        selection=ProviderSelection(ocr="sensitive-ocr", vlm="fake-vlm"),
    )
    request = MediaAnalysisRequest(
        media_id="image-1",
        media_type=MediaType.IMAGE,
        media_uri="s3://media/image-1.png",
        mime_type="image/png",
        sha256=SHA256,
        source_text="联系邮箱 customer@example.com",
    )

    result = await service.analyze(request)

    serialized = result.model_dump_json()
    for secret in (
        "13800138000",
        "11010519491231002X",
        "6222021234567890",
        "customer@example.com",
    ):
        assert secret not in serialized
    assert result.normalized_text_is_untrusted is True
    assert set(result.sensitive_data_categories) == {
        SensitiveDataCategory.PHONE_NUMBER,
        SensitiveDataCategory.IDENTITY_NUMBER,
        SensitiveDataCategory.PAYMENT_ACCOUNT,
        SensitiveDataCategory.EMAIL,
    }


@pytest.mark.asyncio
async def test_malformed_provider_output_becomes_typed_failure() -> None:
    registry = MultimodalProviderRegistry()
    registry.register_asr(MalformedASRProvider())
    service = MultimodalService(
        registry=registry,
        selection=ProviderSelection(asr="malformed-asr"),
    )

    result = await service.analyze(_voice_request())

    assert result.status is MediaAnalysisStatus.FAILED
    assert result.requires_handoff is True
    assert result.stages[0].error is not None
    assert (
        result.stages[0].error.category
        is AnalysisErrorCategory.PROVIDER_FAILURE
    )
