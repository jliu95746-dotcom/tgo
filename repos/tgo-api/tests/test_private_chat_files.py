"""Attachments require tenant authority or a scoped, expiring capability."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from jose import jwt

from app.api.v1.endpoints import chat
from app.core.config import settings
from app.core.security import create_access_token, verify_token
from app.models import ChatFile, Platform, Project, Visitor
from tests.test_conversation_tenant_boundary import channel_app


@pytest.fixture
def private_files(channel_app, tmp_path, monkeypatch):
    app, db, companies, staff, visitors, _ = channel_app
    app.include_router(chat.router, prefix="/chat")
    for model in (Platform, ChatFile):
        model.__table__.create(db.get_bind())
    monkeypatch.setattr(settings, "UPLOAD_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_TYPE", "local")
    monkeypatch.setattr(settings, "API_BASE_URL", "http://test")
    platforms, files = [], []
    for index, company in enumerate(companies):
        platform = Platform(id=visitors[index].platform_id, project_id=company.id,
                            name=company.name, type="website", api_key=f"key-{index}")
        relative_path = f"chat/{company.id}/sample.txt"
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True)
        path.write_bytes(b"private attachment")
        file = ChatFile(project_id=company.id, channel_id=f"{visitors[index].id}-vtr",
                        channel_type=251, file_name="sample.txt", file_path=relative_path,
                        file_size=18, file_type="text/plain", uploaded_by_staff_id=staff[index].id)
        platforms.append(platform)
        files.append(file)
    db.add_all(platforms + files)
    db.commit()
    headers = {"Authorization": "Bearer " + create_access_token(staff[0].username, companies[0].id)}
    return app, db, staff, platforms, files, headers


@pytest.mark.asyncio
async def test_anonymous_and_invalid_staff_token_cannot_read_file(private_files):
    app, _, _, _, files, _ = private_files
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/chat/files/{files[0].id}"
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers={"Authorization": "Bearer invalid"})).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_staff_file_read_and_link_issuance_are_tenant_scoped(private_files, role):
    app, _, staff, _, files, headers = private_files
    staff[0].role = role
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/chat/files/{files[0].id}", headers=headers)).content == b"private attachment"
        assert (await client.get(f"/chat/files/{files[1].id}", headers=headers)).status_code == 404
        assert (await client.post(f"/chat/files/{files[1].id}/access", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_file_link_is_expiring_bound_to_file_and_not_a_login(private_files):
    app, _, _, _, files, headers = private_files
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(f"/chat/files/{files[0].id}/access", headers=headers)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        link = result.json()["access_url"]
        parts = urlsplit(link)
        token = parse_qs(parts.query)["file_token"][0]
        assert verify_token(token) is None
        assert (await client.get(f"/chat/files/{files[0].id}?{parts.query}")).content == b"private attachment"
        assert (await client.get(f"/chat/files/{files[1].id}?{parts.query}")).status_code == 401
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM], audience="yujian-chat-file")
        claims["exp"] = 1
        expired = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        assert (await client.get(f"/chat/files/{files[0].id}", params={"file_token": expired})).status_code == 401


@pytest.mark.asyncio
async def test_platform_cannot_access_another_company_file(private_files):
    app, _, _, platforms, files, _ = private_files
    headers = {"X-Platform-API-Key": platforms[0].api_key}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/chat/files/{files[0].id}", headers=headers)).status_code == 200
        assert (await client.get(f"/chat/files/{files[1].id}", headers=headers)).status_code == 404
        assert (await client.post(f"/chat/files/{uuid4()}/access")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "staff_deleted", "staff_moved", "company_deleted", "file_deleted",
    "visitor_deleted", "platform_disabled", "platform_deleted",
])
async def test_issued_link_rechecks_current_authority(private_files, change):
    app, db, staff, platforms, files, headers = private_files
    if change.startswith("platform_"):
        headers = {"X-Platform-API-Key": platforms[0].api_key}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        issued = await client.post(f"/chat/files/{files[0].id}/access", headers=headers)
        assert issued.status_code == 200
        query = urlsplit(issued.json()["access_url"]).query
        download = f"/chat/files/{files[0].id}?{query}"
        assert (await client.get(download)).status_code == 200
        now = datetime.now(timezone.utc)
        if change == "staff_deleted":
            staff[0].deleted_at = now
        elif change == "staff_moved":
            staff[0].project_id = staff[1].project_id
        elif change == "company_deleted":
            db.get(Project, staff[0].project_id).deleted_at = now
        elif change == "file_deleted":
            files[0].deleted_at = now
        elif change == "visitor_deleted":
            db.get(Visitor, UUID(files[0].channel_id[:-4])).deleted_at = now
        elif change == "platform_disabled":
            platforms[0].is_active = False
        else:
            platforms[0].deleted_at = now
        db.commit()
        assert (await client.get(download)).status_code in (401, 404)


@pytest.mark.asyncio
async def test_private_cloud_read_never_redirects_to_public_url(private_files, monkeypatch):
    app, _, _, _, files, headers = private_files
    reader = AsyncMock(return_value=b"private cloud attachment")
    from app.services import storage

    class PrivateStorage:
        read = reader

        def get_public_url(self, _path):
            pytest.fail("Attachment downloads must not expose cloud URLs")

    monkeypatch.setattr(settings, "STORAGE_TYPE", "minio")
    monkeypatch.setattr(storage, "get_storage", lambda: PrivateStorage())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.get(f"/chat/files/{files[0].id}", headers=headers)
        assert result.status_code == 200
        assert result.content == b"private cloud attachment"
        assert "location" not in result.headers
        assert result.headers["cache-control"] == "private, no-store"
        assert result.headers["referrer-policy"] == "no-referrer"
        reader.assert_awaited_once_with(
            files[0].file_path, max_bytes=settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
        )
        reader.reset_mock()
        assert (await client.get(f"/chat/files/{files[1].id}", headers=headers)).status_code == 404
        reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_metadata_cannot_escape_upload_root(private_files, tmp_path):
    app, db, _, _, files, headers = private_files
    files[0].file_path = "../outside.txt"
    db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        assert (await client.get(f"/chat/files/{files[0].id}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_upload_link_downloads_using_the_real_route_prefix(private_files, monkeypatch):
    app, db, _, _, files, headers = private_files
    app.include_router(chat.router, prefix="/v1/chat")
    from app.services.storage.local import LocalStorageBackend
    from app.services import storage

    monkeypatch.setattr(storage, "get_storage", lambda: LocalStorageBackend(
        settings.UPLOAD_BASE_DIR, settings.API_BASE_URL,
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/chat/upload", headers=headers,
            data={"channel_id": files[0].channel_id, "channel_type": 251},
            files={"file": ("sample.txt", b"new private attachment", "text/plain")},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert db.query(ChatFile).count() == 3
        assert (await client.get(result["file_url"])).content == b"new private attachment"
        assert (await client.get(result["file_url"].split("?")[0])).status_code == 401
