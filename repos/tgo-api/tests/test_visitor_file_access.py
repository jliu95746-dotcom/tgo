"""Public attachment routes must keep the original visitor authority through I/O."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from jose import jwt

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import ChatFile, Visitor
from app.services.im_access import issue_visitor_im_token
from tests.test_private_chat_files import private_files as private_files
from tests.test_conversation_tenant_boundary import channel_app as channel_app


def visitor_headers(db, platform, file):
    visitor = db.get(Visitor, UUID(file.channel_id.removesuffix("-vtr")))
    token = issue_visitor_im_token(visitor, timedelta(hours=1))
    return {
        "X-Platform-API-Key": platform.api_key,
        "Authorization": f"Bearer {token}",
    }, token


@pytest.mark.asyncio
async def test_public_key_and_known_file_are_not_private_authority(private_files):
    app, _, _, platforms, files, _ = private_files
    headers = {"X-Platform-API-Key": platforms[0].api_key}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, path in [
            ("GET", f"/chat/files/{files[0].id}"),
            ("POST", f"/chat/files/{files[0].id}/access"),
        ]:
            response = await client.request(method, path, headers=headers)
            assert response.status_code == 401
            assert b"private attachment" not in response.content


@pytest.mark.asyncio
async def test_visitor_cannot_read_another_visitor_in_the_same_platform(private_files):
    app, db, _, platforms, files, _ = private_files
    headers, _ = visitor_headers(db, platforms[0], files[0])
    other = Visitor(
        project_id=platforms[0].project_id,
        platform_id=platforms[0].id,
        platform_open_id="other-owned-fixture",
    )
    db.add(other)
    db.flush()
    other_file = ChatFile(
        project_id=platforms[0].project_id,
        channel_id=f"{other.id}-vtr",
        channel_type=251,
        file_name="other.txt",
        file_path=files[0].file_path,
        file_size=18,
        file_type="text/plain",
    )
    db.add(other_file)
    db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/chat/files/{other_file.id}", headers=headers)
        assert response.status_code in (401, 404)
        assert b"private attachment" not in response.content


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["retired", "revision", "key_rotation", "expired"])
async def test_visitor_file_link_rechecks_original_session_without_carrying_the_im_grant(
    private_files, change
):
    app, db, _, platforms, files, _ = private_files
    headers, original_token = visitor_headers(db, platforms[0], files[0])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        issued = await client.post(f"/chat/files/{files[0].id}/access", headers=headers)
        assert issued.status_code == 200
        link = issued.json()["access_url"]
        assert original_token not in link
        token = parse_qs(urlsplit(link).query)["file_token"][0]
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience="yujian-chat-file",
        )
        assert claims["actor"] == "visitor"
        assert "im_token" not in claims
        assert (await client.get(link)).content == b"private attachment"
        visitor = db.get(Visitor, UUID(files[0].channel_id[:-4]))
        if change == "retired":
            visitor.access_retired_at = datetime.now(timezone.utc)
        elif change == "revision":
            visitor.access_revision += 1
        elif change == "key_rotation":
            platforms[0].api_key = "owned-rotated-key"
        else:
            claims["exp"] = 1
            token = jwt.encode(
                claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM
            )
            link = f"/chat/files/{files[0].id}?file_token={token}"
        db.commit()
        denied = await client.get(link)
        assert denied.status_code == 401
        assert b"private attachment" not in denied.content


@pytest.mark.asyncio
async def test_cloud_read_checks_authority_after_storage_returns(
    private_files, monkeypatch
):
    app, db, _, platforms, files, _ = private_files
    headers, _ = visitor_headers(db, platforms[0], files[0])
    visitor = db.get(Visitor, UUID(files[0].channel_id[:-4]))

    async def revoke_after_read(*args, **kwargs):
        visitor.access_revision += 1
        db.commit()
        return b"cloud-private-after-revocation"

    reader = AsyncMock(side_effect=revoke_after_read)
    from app.services import storage

    class PrivateStorage:
        read = reader

    monkeypatch.setattr(settings, "STORAGE_TYPE", "minio")
    monkeypatch.setattr(storage, "get_storage", lambda: PrivateStorage())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/chat/files/{files[0].id}", headers=headers)
        assert response.status_code == 401
        assert b"cloud-private-after-revocation" not in response.content
    reader.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_type", ["visitor", "staff"])
async def test_file_capability_expiring_during_storage_read_cannot_return_bytes(
    private_files, monkeypatch, actor_type
):
    from app.services import chat_file_access, storage

    app, db, _, platforms, files, staff_headers = private_files
    headers = (
        visitor_headers(db, platforms[0], files[0])[0]
        if actor_type == "visitor"
        else staff_headers
    )
    expired = False

    class FileClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + (
                timedelta(minutes=10) if expired else timedelta()
            )

    async def slow_read(*args, **kwargs):
        nonlocal expired
        expired = True
        return b"private-file-after-cap-expiry"

    class PrivateStorage:
        read = staticmethod(slow_read)

    monkeypatch.setattr(chat_file_access, "datetime", FileClock)
    monkeypatch.setattr(settings, "STORAGE_TYPE", "minio")
    monkeypatch.setattr(storage, "get_storage", lambda: PrivateStorage())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        issued = await client.post(f"/chat/files/{files[0].id}/access", headers=headers)
        assert issued.status_code == 200
        result = await client.get(issued.json()["access_url"])
        assert result.status_code == 401
        assert b"private-file-after-cap-expiry" not in result.content


@pytest.mark.asyncio
async def test_local_file_is_rechecked_before_its_first_body_chunk(private_files):
    from fastapi.security import HTTPAuthorizationCredentials
    from app.services.chat_file_access import (
        authorize_chat_file,
        chat_file_guard,
        serve_chat_file,
    )

    _, db, _, platforms, files, _ = private_files
    _, token = visitor_headers(db, platforms[0], files[0])
    file, actor = await authorize_chat_file(
        db,
        files[0].id,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        platforms[0].api_key,
    )
    response = await serve_chat_file(file, chat_file_guard(db, file, actor))
    body = []

    async def send(message):
        if message["type"] == "http.response.start":
            visitor = db.get(Visitor, actor.id)
            visitor.access_revision += 1
            db.commit()
        else:
            body.append(message.get("body", b""))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    with pytest.raises(TGOAPIException):
        await response(
            {
                "type": "http",
                "method": "GET",
                "headers": [],
                "extensions": {"http.response.pathsend": {}},
            },
            receive,
            send,
        )
    assert not any(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["missing_grant", "wrong_channel", "storage_revoked"]
)
async def test_visitor_upload_never_persists_unowned_bytes(
    private_files, monkeypatch, change
):
    app, db, _, platforms, files, _ = private_files
    headers, _ = visitor_headers(db, platforms[0], files[0])
    visitor = db.get(Visitor, UUID(files[0].channel_id[:-4]))
    from app.services import storage

    async def save_then_revoke(*args):
        visitor.access_revision += 1
        db.commit()
        return "must-not-be-published"

    upload = AsyncMock(side_effect=save_then_revoke)
    cleanup = AsyncMock(return_value=True)

    class PrivateStorage:
        pass

    backend = PrivateStorage()
    backend.upload = upload
    backend.delete = cleanup
    monkeypatch.setattr(storage, "get_storage", lambda: backend)
    channel = files[0].channel_id
    if change == "missing_grant":
        headers.pop("Authorization")
    elif change == "wrong_channel":
        channel = files[1].channel_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/chat/upload",
            headers=headers,
            data={"channel_id": channel, "channel_type": 251},
            files={"file": ("owned.txt", b"owned-upload", "text/plain")},
        )
        assert result.status_code == 401
        assert "must-not-be-published" not in result.text
    assert db.query(ChatFile).count() == 2
    if change == "storage_revoked":
        upload.assert_awaited_once()
        cleanup.assert_awaited_once_with(upload.call_args.args[1])
    else:
        upload.assert_not_awaited()
        cleanup.assert_not_awaited()
