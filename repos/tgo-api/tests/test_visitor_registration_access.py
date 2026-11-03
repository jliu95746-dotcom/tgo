"""The actual public register endpoint must verify ownership before all mutations."""

from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.v1.endpoints import visitors
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    TGOAPIException,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.models import (
    VisitorAIProfile,
    VisitorAIInsight,
    VisitorSystemInfo,
    VisitorTag,
    Tag,
)
from app.schemas.visitor_access import VisitorAccessInput
from app.services.visitor_access import registration_access
from app.services.im_access import issue_visitor_im_token
from app.services.business_customer_identity import BusinessCustomerIdentityService
from tests.test_visitor_access import access_fixture as access_fixture
from tests.test_business_customer_identity import setup as setup, claims, sign


@pytest.fixture
def register_api(access_fixture, monkeypatch):
    identity, db, visitor, connection, original = access_fixture
    for model in (
        VisitorAIProfile,
        VisitorAIInsight,
        VisitorSystemInfo,
        VisitorTag,
        Tag,
    ):
        model.__table__.create(db.bind)
    channel = AsyncMock()
    im = AsyncMock()
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(visitors.visitor_service, "ensure_visitor_channel", channel)
    monkeypatch.setattr(
        visitors.visitor_service, "upsert_visitor_system_info", lambda *args: False
    )
    monkeypatch.setattr(visitors, "notify_visitor_profile_updated", AsyncMock())
    monkeypatch.setattr(visitors.wukongim_client, "register_or_login_user", im)
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    visitor.platform.api_key = "fixture-only-public-platform-key"
    db.commit()
    app = FastAPI()
    app.include_router(visitors.router, prefix="/v1/visitors")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    from sqlalchemy.orm import Session
    from app.services import visitor_chat_access
    monkeypatch.setattr(visitor_chat_access, "SessionLocal", lambda: Session(db.get_bind()))
    yield app, identity, db, visitor, connection, original, secret, channel, im


@pytest.mark.asyncio
async def test_registration_returns_only_messaging_and_public_profile(register_api):
    app, _, db, visitor, _, _, secret, _, _ = register_api
    visitor.note = "staff-private-note"
    visitor.custom_attributes = {"private-segment": "internal-only"}
    visitor.humanization_skill_name = "staff-private-skill"
    visitor.phone_number = "private-contact"
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/visitors/register", json={
            "platform_api_key": visitor.platform.api_key,
            "platform_open_id": visitor.platform_open_id,
            "visitor_access_token": secret.get_secret_value(),
        })
    assert response.status_code == 201
    data = response.json()
    private_fields = {"note", "custom_attributes", "tags", "ai_profile", "ai_insights", "ai_settings", "system_info", "phone_number", "email", "ip_address", "humanization_skill_name", "assigned_staff_id"}
    assert private_fields.isdisjoint(data)
    assert "staff-private" not in response.text and "internal-only" not in response.text
    assert data["visitor_access_token"] == secret.get_secret_value()
    assert data["id"] == str(visitor.id) and data["im_token"] and data["visitor_end_token"]


@pytest.mark.asyncio
async def test_personal_wechat_only_registers_the_filehelper_test_identity(register_api):
    from app.api.v1.endpoints import platforms

    app, _, db, visitor, _, _, _, _, _ = register_api
    visitor.platform.type = "wechat_local"
    db.commit()
    app.include_router(platforms.router, prefix="/v1/platforms")
    platform_id = visitor.platform.id
    key = visitor.platform.api_key
    endpoint = f"/v1/platforms/{platform_id}/wechat-local/heartbeat"
    heartbeat = AsyncMock(return_value=platforms.WeChatLocalStatus(connected=True))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(platforms, "bridge_status", heartbeat)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            rejected = await client.post("/v1/visitors/register", json={
                "platform_api_key": key,
                "platform_open_id": "wechat-local:0123456789abcdef:friend",
            })
            assert rejected.status_code == 401
            registered = await client.post("/v1/visitors/register", json={
                "platform_api_key": key,
                "platform_open_id": "wechat-local:0123456789abcdef:filehelper",
            })
            assert registered.status_code == 201
            headers = {"X-Platform-API-Key": key}
            assert (await client.post(endpoint, headers=headers)).status_code == 401
            heartbeat.assert_not_awaited()
            headers["Authorization"] = "Bearer " + registered.json()["im_token"]
            response = await client.post(endpoint, headers=headers)
            assert response.status_code == 200
            assert response.json()["connected"] is True
            heartbeat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["channel", "notify", "im"])
async def test_registration_rechecks_original_platform_after_await(register_api, monkeypatch, stage):
    app, _, db, visitor, _, _, secret, channel, im = register_api
    original_key = visitor.platform.api_key

    async def rotate(*_args, **_kwargs):
        visitor.platform.api_key = "replacement-public-key"
        db.commit()

    if stage == "channel":
        channel.side_effect = rotate
    elif stage == "notify":
        monkeypatch.setattr(visitors, "notify_visitor_profile_updated", AsyncMock(side_effect=rotate))
    else:
        im.side_effect = rotate
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/visitors/register", json={
            "platform_api_key": original_key, "platform_open_id": visitor.platform_open_id,
            "visitor_access_token": secret.get_secret_value(), "name": "own-name",
        })
    assert response.status_code == 401
    assert secret.get_secret_value() not in response.text
    if stage != "im":
        assert im.await_count == 0


@pytest.mark.asyncio
async def test_registration_response_revoked_before_bytes_cannot_issue_credentials(register_api, monkeypatch):
    app, _, db, visitor, _, _, secret, _, _ = register_api
    original = visitors.registration_output

    def revoke(*args, **kwargs):
        reply, guard = original(*args, **kwargs)
        visitor.access_revision += 1
        db.commit()
        return reply, guard

    monkeypatch.setattr(visitors, "registration_output", revoke)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/visitors/register", json={
            "platform_api_key": visitor.platform.api_key, "platform_open_id": visitor.platform_open_id,
            "visitor_access_token": secret.get_secret_value(),
        })
    assert response.status_code == 401
    assert secret.get_secret_value() not in response.text and "im_token" not in response.text


@pytest.mark.asyncio
async def test_registration_response_rechecks_after_headers(register_api):
    from app.api.current_access_route import CurrentAccessResponse
    from app.schemas.visitor import VisitorRegisterResponse
    from app.services.visitor_registration_output import RegistrationOrigin, registration_output
    from app.services.visitor_access import require_access
    from starlette.responses import Response

    _, _, db, visitor, _, _, secret, _, _ = register_api
    business = require_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    db.commit()
    origin = RegistrationOrigin(visitor.project_id, visitor.platform_id, SecretStr(visitor.platform.api_key))
    token = issue_visitor_im_token(visitor, timedelta(hours=1), business=business)
    reply, guard = registration_output(db, origin, visitor, token, secret, business, "zh")
    assert isinstance(reply, VisitorRegisterResponse)
    response = CurrentAccessResponse(Response(reply.model_dump_json(), media_type="application/json"), guard)
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            visitor.platform.is_active = False
            db.commit()

    with pytest.raises(TGOAPIException):
        await response({"type": "http", "method": "POST", "headers": []}, AsyncMock(), send)
    assert len(sent) == 1 and sent[0]["type"] == "http.response.start"


@pytest.mark.asyncio
async def test_public_open_id_cannot_overwrite_or_mint_existing_access(register_api):
    app, _, db, visitor, _, _, _, channel, im = register_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/visitors/register",
            json={
                "platform_api_key": visitor.platform.api_key,
                "platform_open_id": visitor.platform_open_id,
                "name": "untrusted-overwrite",
            },
        )
    assert result.status_code in {401, 403}
    db.refresh(visitor)
    assert visitor.name is None
    assert channel.await_count == 0 and im.await_count == 0


@pytest.mark.asyncio
async def test_owned_ordinary_resume_returns_secret_without_leaking_storage_digest(
    register_api,
):
    app, _, db, visitor, _, _, secret, _, _ = register_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/visitors/register",
            json={
                "platform_api_key": visitor.platform.api_key,
                "platform_open_id": visitor.platform_open_id,
                "visitor_access_token": secret.get_secret_value(),
                "name": "owned-profile",
            },
        )
    assert result.status_code == 201
    assert result.json()["visitor_access_token"] == secret.get_secret_value()
    assert result.headers["cache-control"] == "no-store"
    assert isinstance(result.json()["visitor_end_token"], str)
    assert result.json()["visitor_end_token"] not in repr(result)
    db.refresh(visitor)
    assert (
        visitor.name == "owned-profile"
        and visitor.access_secret_digest not in result.text
    )


@pytest.mark.asyncio
async def test_actual_registration_end_grant_ends_access_without_preserving_read_secret(register_api, monkeypatch):
    from app.api.v1.endpoints import visitor_access
    from app.services import visitor_access_end

    app, _, db, visitor, _, _, secret, _, _ = register_api
    app.include_router(visitor_access.router, prefix="/v1/visitors")
    monkeypatch.setattr(visitor_access_end.wukongim_client, "enabled", False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        registered = await client.post("/v1/visitors/register", json={
            "platform_api_key":visitor.platform.api_key,"visitor_id":str(visitor.id),"visitor_access_token":secret.get_secret_value()})
        assert registered.status_code == 201
        grant = registered.json()["visitor_end_token"]
        ended = await client.post("/v1/visitors/access/end", json={
            "platform_api_key":visitor.platform.api_key,"visitor_id":str(visitor.id),"visitor_end_token":grant})
        assert ended.status_code == 200 and ended.json()["status"] == "ended"
        repeated = await client.post("/v1/visitors/access/end", json={
            "platform_api_key":visitor.platform.api_key,"visitor_id":str(visitor.id),"visitor_end_token":grant})
        assert repeated.status_code == 200
        assert grant not in ended.text and secret.get_secret_value() not in ended.text
        failed = await client.post("/v1/visitors/register", json={
            "platform_api_key":visitor.platform.api_key,"visitor_id":str(visitor.id),"im_token":grant})
        assert failed.status_code == 401
    db.refresh(visitor)
    assert visitor.access_retired_at is not None


@pytest.mark.asyncio
async def test_protected_resume_requires_fresh_customer_not_cached_im(register_api):
    app, identity, _, visitor, connection, original, _, channel, im = register_api
    receipt = identity.exchange(connection, sign(claims(original)))
    old_token = issue_visitor_im_token(visitor, timedelta(hours=1))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/visitors/register",
            json={
                "platform_api_key": visitor.platform.api_key,
                "platform_open_id": visitor.platform_open_id,
                "im_token": old_token,
            },
        )
        owned = await client.post(
            "/v1/visitors/register",
            json={
                "platform_api_key": visitor.platform.api_key,
                "platform_open_id": visitor.platform_open_id,
                "im_token": old_token,
                "customer_sessions": [
                    {
                        "connection_id": str(connection),
                        "customer_session": receipt.session_token.get_secret_value(),
                    }
                ],
            },
        )
    assert result.status_code in {401, 403}
    assert owned.status_code == 201 and owned.json()["business_protected"] is True
    assert owned.json()["visitor_access_token"]
    assert receipt.session_token.get_secret_value() not in owned.text
    assert channel.await_count == 1 and im.await_count == 1


@pytest.mark.asyncio
async def test_invalid_registration_fields_never_echo_secret(register_api):
    app, _, _, visitor, _, _, _, _, _ = register_api
    private = "unparsed-visitor-private-fixture"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/visitors/register",
            json={
                "platform_api_key": visitor.platform.api_key,
                "visitor_access_token": {"private": private},
            },
        )
    assert result.status_code == 422 and private not in result.text


def test_first_binding_cannot_adopt_known_visitor_without_possession(register_api):
    _, _, db, visitor, connection, original, secret, _, _ = register_api
    actor = BusinessCustomerIdentityService(db)
    assertion = sign(
        claims(original, customer_id="authenticated-user-without-browser-possession")
    )
    with pytest.raises(TGOAPIException):
        actor.exchange(connection, assertion)
    with pytest.raises(TGOAPIException):
        actor.exchange(
            connection,
            assertion,
            visitor_access_token=SecretStr("different-visitor-credential"),
        )
    with pytest.raises(TGOAPIException):
        actor.exchange(connection, assertion, trusted_channel=True)
    bound = actor.exchange(connection, assertion, visitor_access_token=secret)
    assert bound.visitor_id == visitor.id
