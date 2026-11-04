"""A private channel service cannot use public IDs or metadata as browser authority."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.internal.endpoints.channel_visitors import router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.models.visitor import Visitor
from app.services import visitor_service
from tests.test_visitor_registration_access import register_api as register_api
from tests.test_visitor_access import access_fixture as access_fixture
from tests.test_business_customer_identity import setup as setup

TOKEN = "fixture-only-private-channel-service-token"


@pytest.fixture
def channel_api(register_api, monkeypatch):
    _, _, db, visitor, _, _, _, _, _ = register_api
    visitor.platform.type = "wecom"
    visitor.access_secret_digest = None
    db.commit()
    monkeypatch.setattr(settings, "SAAS_INTERNAL_TOKEN", SecretStr(TOKEN))
    monkeypatch.setattr(visitor_service, "ensure_visitor_channel", AsyncMock())

    async def create(**fields):
        value = Visitor(
            project_id=fields["platform"].project_id,
            platform_id=fields["platform"].id,
            platform_open_id=fields["platform_open_id"],
            nickname=fields.get("nickname"),
        )
        db.add(value)
        db.commit()
        return value

    monkeypatch.setattr(visitor_service, "create_visitor_with_channel", create)
    app = FastAPI()
    app.include_router(router, prefix="/internal/channel-visitors")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    return (
        app,
        db,
        visitor,
        {
            "platform_api_key": visitor.platform.api_key,
            "project_id": str(visitor.project_id),
            "platform_type": "wecom",
            "platform_open_id": visitor.platform_open_id,
        },
    )


@pytest.mark.asyncio
async def test_private_channel_auth_checks_before_existing_profiles(channel_api):
    app, db, visitor, body = channel_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for headers in ({}, {"X-SaaS-Service-Token": body["platform_api_key"]}):
            denied = await client.post(
                "/internal/channel-visitors/register",
                json={**body, "nickname": "forged"},
                headers=headers,
            )
            assert denied.status_code == 403
    db.refresh(visitor)
    assert visitor.nickname != "forged" and visitor.access_secret_digest is None


@pytest.mark.asyncio
async def test_private_channel_returns_only_identity_and_no_browser_grant(channel_api):
    app, db, visitor, body = channel_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/internal/channel-visitors/register",
            json=body,
            headers={"X-SaaS-Service-Token": TOKEN},
        )
        new_body = {**body, "platform_open_id": "another-verified-external-user"}
        new = await client.post(
            "/internal/channel-visitors/register",
            json=new_body,
            headers={"X-SaaS-Service-Token": TOKEN},
        )
        again = await client.post(
            "/internal/channel-visitors/register",
            json=new_body,
            headers={"X-SaaS-Service-Token": TOKEN},
        )
    assert first.status_code == new.status_code == again.status_code == 201
    assert new.json()["id"] == again.json()["id"] and new.json()["id"] != str(
        visitor.id
    )
    for result in (first, new, again):
        assert (
            not {
                "im_token",
                "visitor_access_token",
                "customer_sessions",
                "access_secret_digest",
            }
            & result.json().keys()
        )
        assert result.headers["cache-control"] == "no-store"
    db.refresh(visitor)
    assert visitor.access_secret_digest is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["project", "type", "website", "inactive"])
async def test_private_channel_requires_actual_project_and_type(channel_api, fault):
    app, db, visitor, body = channel_api
    if fault == "project":
        body = {**body, "project_id": str(uuid4())}
    if fault == "type":
        body = {**body, "platform_type": "wechat"}
    if fault == "website":
        visitor.platform.type = "website"
        db.commit()
        body = {**body, "platform_type": "website"}
    if fault == "inactive":
        visitor.platform.is_active = False
        db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post(
            "/internal/channel-visitors/register",
            json=body,
            headers={"X-SaaS-Service-Token": TOKEN},
        )
    assert denied.status_code == 403
