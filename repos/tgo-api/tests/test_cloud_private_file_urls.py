"""Private API links must survive both cloud backends and media forwarding."""

from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.services import platform_message_client
from app.services.storage.aliyun_oss import AliyunOSSBackend
from app.services.storage.minio import MinIOBackend
from tests.test_conversation_tenant_boundary import channel_app  # noqa: F401
from tests.test_private_chat_files import private_files  # noqa: F401


@pytest.fixture(params=["oss", "minio"])
def cloud_storage(request, monkeypatch):
    # Exercise real URL methods without constructing cloud SDK clients.
    if request.param == "oss":
        storage = object.__new__(AliyunOSSBackend)
        storage.bucket_url = "https://objects.example.test"
    else:
        storage = object.__new__(MinIOBackend)
        storage.download_url = "https://objects.example.test"
        storage.bucket_name = "private-files"
    monkeypatch.setattr(
        settings, "API_BASE_URL", "https://api.example.test/api"
    )
    return storage


@pytest.mark.parametrize(
    "prefix",
    [
        "/v1",
        "/api/v1",
        "http://localhost:5173/api/v1",
        "http://127.0.0.1:5173/v1",
        "http://[::1]:5173/api/v1",
    ],
)
def test_private_links_keep_api_route_and_exact_query(cloud_storage, prefix):
    file_id = uuid4()
    query = "file_token=abc%2Bdef%2Fghi&part=1&part=2"
    source = f"{prefix}/chat/files/{file_id}?{query}#preview"
    assert cloud_storage.resolve_url(source) == (
        f"https://api.example.test/api/v1/chat/files/{file_id}"
        f"?{query}#preview"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost.example.test/assets/photo.png?signature=keep",
        "https://127.0.0.1.example.test/assets/photo.png?signature=keep",
        "https://cdn.example.test/assets/photo.png?host=localhost",
        "https://api.example.test/api/v1/chat/files/"
        "73dd5a94-df5a-4b1a-bc1b-9027a58cc931?file_token=keep",
    ],
)
def test_external_urls_are_not_rewritten(cloud_storage, url):
    assert cloud_storage.resolve_url(url) == url


def test_non_attachment_object_paths_keep_cloud_behavior(cloud_storage):
    assert cloud_storage.resolve_url("/assets/logo.png") == (
        cloud_storage.get_public_url("/assets/logo.png")
    )


def test_nested_forwarded_media_keeps_private_links(
    cloud_storage, monkeypatch
):
    monkeypatch.setattr(
        platform_message_client,
        "get_storage",
        lambda: cloud_storage,
    )
    path = f"/v1/chat/files/{uuid4()}?file_token=keep%2Bsignature"
    payload = {"images": [{"url": path}], "attachment": {"file_url": path}}
    resolved = platform_message_client.resolve_media_urls(payload)
    expected = "https://api.example.test/api" + path
    assert resolved == {
        "images": [{"url": expected}],
        "attachment": {"file_url": expected},
    }
    assert payload["images"][0]["url"] == path


@pytest.mark.asyncio
async def test_resolved_cloud_link_downloads_through_auth_route(
    private_files,  # noqa: F811
    cloud_storage,
    monkeypatch,
):
    app, _, _, _, files, headers = private_files
    from app.api.v1.endpoints import chat

    app.include_router(chat.router, prefix="/v1/chat")
    monkeypatch.setattr(settings, "API_BASE_URL", "http://test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        issued = await client.post(
            f"/v1/chat/files/{files[0].id}/access",
            headers=headers,
        )
        assert issued.status_code == 200
        link = urlsplit(issued.json()["access_url"])
        frontend_link = f"http://localhost:5173/api{link.path}?{link.query}"
        resolved = cloud_storage.resolve_url(frontend_link)
        response = await client.get(resolved)
        assert response.status_code == 200
        assert response.content == b"private attachment"
        assert response.headers["cache-control"] == "private, no-store"
        assert (await client.get(resolved.split("?")[0])).status_code == 401
