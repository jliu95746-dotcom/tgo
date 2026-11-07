"""Storage calls conform to the installed SDKs without contacting a bucket."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from oss2.models import GetObjectResult

from app.services.storage.aliyun_oss import AliyunOSSBackend
from app.services.storage.minio import MinIOBackend


def s3_backend() -> MinIOBackend:
    return MinIOBackend(
        "https://storage.invalid", "synthetic", "synthetic", "sample"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [4, 12])
async def test_s3_real_stream_response_and_size_guard(
    content_length: int,
) -> None:
    backend = s3_backend()
    data = BytesIO(b"data")
    stream = StreamingBody(data, content_length)
    with Stubber(backend.s3) as stub:
        stub.add_response(
            "get_object",
            {"Body": stream, "ContentLength": content_length},
            {"Bucket": "sample", "Key": "chat/image.png"},
        )
        if content_length > 10:
            with pytest.raises(ValueError, match="read limit"):
                await backend.read("chat/image.png", max_bytes=10)
        else:
            assert (
                await backend.read("chat/image.png", max_bytes=10) == b"data"
            )
        stub.assert_no_pending_responses()
    assert data.closed


@pytest.mark.asyncio
async def test_s3_upload_exists_delete_keep_sdk_request_contract() -> None:
    backend = s3_backend()
    body = BytesIO(b"data")
    with Stubber(backend.s3) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "sample",
                "Key": "chat/image.png",
                "Body": body,
                "ContentType": "image/png",
            },
        )
        stub.add_response(
            "head_object",
            {},
            {
                "Bucket": "sample",
                "Key": "chat/image.png",
            },
        )
        stub.add_response(
            "delete_object",
            {},
            {
                "Bucket": "sample",
                "Key": "chat/image.png",
            },
        )
        assert await backend.upload(body, "/chat/image.png", "image/png") == (
            "https://storage.invalid/sample/chat/image.png"
        )
        assert await backend.exists("/chat/image.png")
        assert await backend.delete("/chat/image.png")
        stub.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_s3_sdk_client_error_keeps_false_result() -> None:
    backend = s3_backend()
    with Stubber(backend.s3) as stub:
        for operation in ["head_object", "delete_object"]:
            stub.add_client_error(
                operation,
                service_error_code="NoSuchKey",
                http_status_code=404,
                expected_params={"Bucket": "sample", "Key": "absent"},
            )
        assert not await backend.exists("absent")
        assert not await backend.delete("absent")
        stub.assert_no_pending_responses()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [None, "4", "12"])
async def test_oss_real_result_reads_and_closes_response(
    content_length,
) -> None:
    backend = AliyunOSSBackend(
        "oss.invalid", "sample", "synthetic", "synthetic"
    )
    body = BytesIO(b"data")
    response = SimpleNamespace(
        status=200,
        request_id="synthetic",
        headers=(
            {}
            if content_length is None
            else {"content-length": content_length}
        ),
        read=body.read,
        response=body,
    )
    result = GetObjectResult(response)
    backend.bucket.get_object = Mock(return_value=result)
    if content_length == "12":
        with pytest.raises(ValueError, match="read limit"):
            await backend.read("chat/image.png", max_bytes=10)
    else:
        assert await backend.read("chat/image.png", max_bytes=10) == b"data"
    backend.bucket.get_object.assert_called_once_with("chat/image.png")
    assert body.closed
