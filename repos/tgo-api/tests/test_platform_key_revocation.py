"""Invalid platform keys cannot authorize operations or old capabilities."""

from datetime import timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Platform, Visitor
from app.services import visitor_chat_access as chat_access
from app.services import visitor_data_access as data_access
from app.services import chat_file_access as file_access
from app.services.im_access import issue_visitor_im_token
from tests import test_im_access as im_fixtures
from tests import test_private_chat_files as file_fixtures
from tests import test_conversation_tenant_boundary as channel_fixtures

records = im_fixtures.records
channel_app = channel_fixtures.channel_app
private_files = file_fixtures.private_files
INVALID_KEYS = [None, "", " ", " \t "]


@pytest.mark.parametrize(
    "value", [None, "", " ", 7, True, b"owned-key", [], {}]
)
def test_key_reader_does_not_coerce_invalid_secret_types(value):
    assert (
        data_access.read_platform_api_key(SimpleNamespace(api_key=value))
        is None
    )


def test_invalid_unicode_is_rejected_without_encoding_errors():
    malformed = chr(0xD800)
    assert (
        data_access.read_platform_api_key(SimpleNamespace(api_key=malformed))
        is None
    )
    assert not data_access.platform_api_key_matches(
        SimpleNamespace(api_key="owned-key"), malformed
    )


@pytest.mark.parametrize("key", ["owned-key", " owned-key ", "合成密钥"])
def test_valid_keys_are_preserved_and_compared_without_normalization(key):
    platform = SimpleNamespace(api_key=key)
    assert data_access.read_platform_api_key(platform) == key
    assert data_access.platform_api_key_matches(platform, key)
    assert not data_access.platform_api_key_matches(platform, key + "x")


def bearer(visitor):
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=issue_visitor_im_token(visitor, timedelta(hours=1)),
    )


async def original_access(records, monkeypatch, mode):
    db, _, _, platforms, visitors = records
    monkeypatch.setattr(
        chat_access, "SessionLocal", lambda: Session(db.get_bind())
    )
    if mode == "website":
        return data_access.require_visitor_data_access(
            db, platforms[0], bearer(visitors[0])
        )
    platforms[0].type = "wecom_kf"
    db.commit()
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("owned-private-service")
    )
    monkeypatch.setattr(
        chat_access,
        "get_or_create_channel_visitor",
        AsyncMock(return_value=(visitors[0], False)),
    )
    _, _, access = await chat_access.resolve_chat_visitor(
        db,
        platforms[0],
        visitors[0].platform_open_id,
        None,
        "owned-private-service",
    )
    return access


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["website", "private_channel"])
@pytest.mark.parametrize("key", INVALID_KEYS)
async def test_invalid_key_cannot_issue_an_original_grant(
    records, monkeypatch, mode, key
):
    db, _, _, platforms, visitors = records
    platforms[0].api_key = key
    if mode == "private_channel":
        platforms[0].type = "wecom_kf"
    db.commit()
    create = AsyncMock(return_value=(visitors[0], False))
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("owned-private-service")
    )
    monkeypatch.setattr(chat_access, "get_or_create_channel_visitor", create)
    with pytest.raises(TGOAPIException) as failure:
        if mode == "website":
            data_access.require_visitor_data_access(
                db, platforms[0], bearer(visitors[0])
            )
        else:
            await chat_access.resolve_chat_visitor(
                db,
                platforms[0],
                visitors[0].platform_open_id,
                None,
                "owned-private-service",
            )
    assert failure.value.status_code == 401
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["website", "private_channel"])
@pytest.mark.parametrize("key", INVALID_KEYS)
async def test_cleared_key_denies_a_previously_issued_grant(
    records, monkeypatch, mode, key
):
    db, _, _, platforms, _ = records
    access = await original_access(records, monkeypatch, mode)
    assert chat_access.check_chat_access(db, access) is not None
    with Session(db.get_bind()) as other:
        other.get(Platform, platforms[0].id).api_key = key
        other.commit()
    with pytest.raises(TGOAPIException) as failure:
        chat_access.check_chat_access(db, access)
    assert failure.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["website", "private_channel"])
@pytest.mark.parametrize("boundary", ["before", "during"])
@pytest.mark.parametrize("key", [None, ""])
async def test_key_revocation_at_await_boundaries_stops_operations_or_results(
    records, monkeypatch, mode, boundary, key
):
    db, _, _, platforms, _ = records
    access = await original_access(records, monkeypatch, mode)

    def revoke():
        platforms[0].api_key = key
        db.commit()

    async def operation():
        revoke()
        return "synthetic-private-result"

    work = AsyncMock(side_effect=operation)
    if boundary == "before":
        revoke()
    with pytest.raises(TGOAPIException) as failure:
        await chat_access.guarded_chat_call(work, access)
    assert failure.value.status_code == 401
    assert work.await_count == int(boundary == "during")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["website", "private_channel"])
@pytest.mark.parametrize("boundary", ["headers", "body"])
async def test_response_guard_rechecks_invalid_key_before_releasing_bytes(
    records, monkeypatch, mode, boundary
):
    db, _, _, platforms, _ = records
    access = await original_access(records, monkeypatch, mode)
    sent = []

    def revoke():
        platforms[0].api_key = None
        db.commit()

    if boundary == "headers":
        revoke()

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            revoke()

    response = chat_access.AuthorizedChatResponse(
        Response("synthetic-private-result"), access
    )
    with pytest.raises(TGOAPIException) as failure:
        await response({"type": "http"}, AsyncMock(), send)
    assert failure.value.status_code == 401
    assert all(item["type"] != "http.response.body" for item in sent)
    assert len(sent) == int(boundary == "body")


@pytest.mark.asyncio
@pytest.mark.parametrize("key", INVALID_KEYS)
async def test_old_file_capability_is_denied_after_key_is_cleared(
    private_files, key
):
    app, db, _, platforms, files, staff_headers = private_files
    visitor = db.get(Visitor, UUID(files[0].channel_id[:-4]))
    headers = {
        "X-Platform-API-Key": platforms[0].api_key,
        "Authorization": "Bearer " + bearer(visitor).credentials,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        issued = await client.post(
            f"/chat/files/{files[0].id}/access", headers=headers
        )
        assert issued.status_code == 200
        query = urlsplit(issued.json()["access_url"]).query
        platforms[0].api_key = key
        db.commit()
        denied = await client.get(f"/chat/files/{files[0].id}?{query}")
        assert denied.status_code == 401
        assert b"private attachment" not in denied.content
        # Staff authority has its own lifecycle and remains valid here.
        own = await client.get(
            f"/chat/files/{files[0].id}", headers=staff_headers
        )
        assert own.status_code == 200 and own.content == b"private attachment"


@pytest.mark.asyncio
async def test_file_actor_cannot_adopt_a_key_changed_after_visitor_snapshot(
    private_files, monkeypatch
):
    _, db, _, platforms, files, _ = private_files
    visitor = db.get(Visitor, UUID(files[0].channel_id[:-4]))
    original_key = platforms[0].api_key
    original_require = file_access.require_visitor_data_access

    def rotate_after_snapshot(session, platform, credentials):
        proof = original_require(session, platform, credentials)
        platform.api_key = "changed-after-original-proof"
        session.commit()
        return proof

    monkeypatch.setattr(
        file_access, "require_visitor_data_access", rotate_after_snapshot
    )
    actor = file_access.authenticate_file_actor(
        db, bearer(visitor), original_key
    )
    assert (
        actor.platform_key_digest
        == hashlib.sha256(original_key.encode()).hexdigest()
    )
    with pytest.raises(TGOAPIException) as failure:
        await file_access.authorize_file_channel(
            db, actor, files[0].channel_id, 251
        )
    assert failure.value.status_code == 401
