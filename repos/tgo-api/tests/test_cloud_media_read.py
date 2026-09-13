"""Cloud media reads use stored keys and always close their bounded response."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.storage.minio import MinIOBackend
from app.services.storage.aliyun_oss import AliyunOSSBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_type", [MinIOBackend, AliyunOSSBackend])
@pytest.mark.parametrize("oversized", [False, True])
async def test_cloud_read_is_bounded_and_closed(backend_type, oversized):
    backend = object.__new__(backend_type)
    stream = SimpleNamespace(
        content_length=12 if oversized else 4,
        read=Mock(return_value=b"data"),
        close=Mock(),
    )
    if backend_type is MinIOBackend:
        backend.bucket_name = "owned-test-bucket"
        getter = Mock(
            return_value={"Body": stream, "ContentLength": stream.content_length}
        )
        backend.s3 = SimpleNamespace(get_object=getter)
    else:
        getter = Mock(return_value=stream)
        backend.bucket = SimpleNamespace(get_object=getter)
    if oversized:
        with pytest.raises(ValueError):
            await backend.read("chat/owned/image.png", max_bytes=10)
        stream.read.assert_not_called()
    else:
        assert await backend.read("chat/owned/image.png", max_bytes=10) == b"data"
        stream.read.assert_called_once_with(11)
    stream.close.assert_called_once()
    getter.assert_called_once()
