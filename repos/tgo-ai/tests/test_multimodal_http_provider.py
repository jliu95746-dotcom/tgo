"""Wire-level media tests: actual bytes, bounded errors, no tool execution."""

import hashlib
import json

import httpx
import pytest

from app.runtime.multimodal.providers.http_provider import (
    ConfiguredMediaModel,
    HTTPMediaProvider,
    validate_media_content,
)
from app.runtime.multimodal.providers.base import ProviderExecutionError
from app.schemas.multimodal import (
    ASRRequest,
    AnalysisErrorCategory,
    MediaAnalysisRequest,
    MediaType,
    OCRRequest,
    VLMRequest,
)


IMAGE = b"\x89PNG\r\n\x1a\n" + b"owned-test-image"
AUDIO = b"RIFF" + b"\x00" * 4 + b"WAVEfmt " + b"owned-test-audio"


def media_request(content=IMAGE, mime="image/png", kind=MediaType.IMAGE):
    return MediaAnalysisRequest(
        media_id="owned-media",
        media_type=kind,
        media_uri="tgo-media://owned-test/media",
        mime_type=mime,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def model(capability="ocr"):
    return ConfiguredMediaModel(
        name="owned-provider",
        model="owned-" + capability,
        api_base_url="https://provider.example/v1",
        api_key="fixture-only-key",
        timeout_seconds=2,
    )


def provider_request(schema, request):
    return schema(
        **request.model_dump(exclude={"media_type", "source_text", "language"})
    )


@pytest.mark.asyncio
async def test_image_wire_contains_bytes_not_internal_uri_and_no_tools():
    calls = []

    async def transport(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-only-key"
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        assert "tgo-media" not in request.content.decode()
        assert "tools" not in body and "tool_ids" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":"订单 A1001"}'},
                    }
                ]
            },
        )

    request = media_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model(), validate_media_content(request, IMAGE), client
        )
        result = await provider.extract_text(provider_request(OCRRequest, request))
    assert result.text == "订单 A1001" and result.model_version == "owned-ocr"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_asr_uploads_file_bytes_and_normalizes_language_tag():
    async def transport(request):
        body = await request.aread()
        assert request.url.path == "/v1/audio/transcriptions"
        assert "multipart/form-data" in request.headers["content-type"]
        assert AUDIO in body
        assert b'name="language"\r\n\r\nzh\r\n' in body
        assert b'name="model"\r\n\r\nowned-asr\r\n' in body
        return httpx.Response(200, json={"text": "我要查物流"})

    request = media_request(AUDIO, "audio/wav", MediaType.VOICE)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model("asr"), validate_media_content(request, AUDIO), client
        )
        result = await provider.transcribe(
            ASRRequest(
                **provider_request(ASRRequest, request).model_dump(
                    exclude={"language"}
                ),
                language="zh-CN",
            )
        )
    assert result.transcript == "我要查物流"


@pytest.mark.parametrize(
    "status,retryable",
    [(401, False), (400, False), (429, True), (500, True), (302, False)],
)
@pytest.mark.asyncio
async def test_provider_errors_do_not_leak_bodies_or_follow_redirects(
    status, retryable
):
    calls = []

    async def transport(request):
        calls.append(request.url)
        return httpx.Response(
            status,
            headers={"Location": "https://wrong.example"},
            text="secret upstream credential",
        )

    request = media_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model("vlm"), validate_media_content(request, IMAGE), client
        )
        with pytest.raises(ProviderExecutionError) as error:
            await provider.describe(provider_request(VLMRequest, request))
    assert "secret" not in str(error.value)
    assert error.value.retryable is retryable
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": "半句话"}}]},
        {
            "choices": [
                {"finish_reason": "stop", "message": {"content": " ", "tool_calls": []}}
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {"content": "结果", "tool_calls": [{}]},
                }
            ]
        },
    ],
)
@pytest.mark.asyncio
async def test_incomplete_or_tool_responses_fail_instead_of_becoming_image_facts(body):
    async def transport(request):
        return httpx.Response(200, json=body)

    request = media_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model("vlm"), validate_media_content(request, IMAGE), client
        )
        with pytest.raises(ProviderExecutionError):
            await provider.describe(provider_request(VLMRequest, request))


@pytest.mark.asyncio
async def test_no_visible_text_is_a_successful_ocr_observation():
    async def transport(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":""}'},
                    }
                ]
            },
        )

    request = media_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model(), validate_media_content(request, IMAGE), client
        )
        result = await provider.extract_text(provider_request(OCRRequest, request))
    assert result.text == "（未识别到可辨认文字）"


@pytest.mark.parametrize(
    "content,mime",
    [
        (b"not an image", "image/png"),
        (IMAGE, "image/jpeg"),
        (b"#!AMR\nvoice", "audio/amr"),
    ],
)
def test_unsupported_or_mismatched_media_fails_before_network(content, mime):
    kind = MediaType.IMAGE if mime.startswith("image") else MediaType.VOICE
    with pytest.raises(ProviderExecutionError) as error:
        validate_media_content(media_request(content, mime, kind), content)
    assert error.value.category is AnalysisErrorCategory.INVALID_MEDIA


def test_hash_and_size_are_verified_before_network():
    with pytest.raises(ProviderExecutionError):
        validate_media_content(media_request(), IMAGE + b"changed")
    with pytest.raises(ProviderExecutionError):
        validate_media_content(media_request(), IMAGE, max_bytes=4)


@pytest.mark.asyncio
async def test_request_identity_must_match_validated_bytes():
    calls = []

    async def transport(request):
        calls.append(request)
        return httpx.Response(500)

    request = media_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model(), validate_media_content(request, IMAGE), client
        )
        wrong = provider_request(OCRRequest, request).model_copy(
            update={"media_id": "another-file"}
        )
        with pytest.raises(ProviderExecutionError):
            await provider.extract_text(wrong)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["oversized", "timeout"])
async def test_bounded_response_and_timeout_are_terminal_errors(problem):
    async def transport(request):
        if problem == "timeout":
            raise httpx.ReadTimeout("secret upstream address", request=request)
        return httpx.Response(200, content=b"x" * 300000)

    request = media_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            model("vlm"), validate_media_content(request, IMAGE), client
        )
        with pytest.raises(ProviderExecutionError) as error:
            await provider.describe(provider_request(VLMRequest, request))
    assert "secret" not in str(error.value)
    if problem == "timeout":
        assert error.value.category is AnalysisErrorCategory.TIMEOUT
