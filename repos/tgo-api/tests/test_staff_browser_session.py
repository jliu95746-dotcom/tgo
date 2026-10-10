"""Rolling browser sessions expire on inactivity, never background traffic."""

from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import staff as endpoints
from app.core import security
from app.core.database import get_db
from app.core.config import settings
from app.models import Project, Staff


@pytest.mark.asyncio
async def test_existing_valid_login_can_adopt_persistent_session(browser_app):
    app, _, member, project, redis = browser_app
    token = security.create_access_token(member.username, project.id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.post(
            "/v1/staff/session/refresh?active=true",
            headers={**SESSION_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert client.cookies.get("tgo-staff-session")
        assert len(redis.values) == 1


@pytest.mark.asyncio
async def test_expired_bearer_alone_cannot_start_a_browser_session(
    browser_app,
):
    app, _, member, project, redis = browser_app
    token = security.create_access_token(
        member.username,
        project.id,
        expires_delta=timedelta(seconds=-1),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.post(
            "/v1/staff/session/refresh?active=true",
            headers={**SESSION_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        assert not redis.values


WEEK = 7 * 24 * 60 * 60
SESSION_HEADERS = {"X-TGO-Session": "1"}


class FakeRedis:
    def __init__(self):
        self.now = 0
        self.values = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def setex(self, key, seconds, value):
        self.values[key] = (value, self.now + seconds)

    async def delete(self, key):
        return self.values.pop(key, None) is not None

    async def eval(self, script, count, key, active, seconds):
        entry = self.values.get(key)
        if entry is None or entry[1] <= self.now:
            self.values.pop(key, None)
            return None
        value, deadline = entry
        if int(active):
            deadline = self.now + int(seconds)
            self.values[key] = (value, deadline)
        return [value, deadline - self.now]


@pytest.fixture
def browser_app(monkeypatch):
    from app.services import staff_browser_session

    redis = FakeRedis()
    monkeypatch.setattr(
        staff_browser_session.settings, "REDIS_URL", "redis://fixture"
    )
    monkeypatch.setattr(
        staff_browser_session.Redis, "from_url", lambda *args, **kwargs: redis
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    with Session(engine) as db:
        project = Project(name="会话测试企业", api_key="fixture-session-project")
        db.add(project)
        db.flush()
        member = Staff(
            project_id=project.id,
            username="session@example.com",
            password_hash="unused",
            role="admin",
        )
        db.add(member)
        db.commit()
        monkeypatch.setattr(
            endpoints, "authenticate_user", lambda *args: member
        )
        monkeypatch.setattr(
            endpoints.wukongim_client, "register_or_login_user", AsyncMock()
        )
        monkeypatch.setattr(
            endpoints, "ensure_project_staff_channel", AsyncMock()
        )
        monkeypatch.setattr(
            staff_browser_session.wukongim_client,
            "register_or_login_user",
            AsyncMock(),
        )
        app = FastAPI()
        app.dependency_overrides[get_db] = lambda: db
        app.include_router(endpoints.router, prefix="/v1/staff")
        yield app, db, member, project, redis
    engine.dispose()


async def login(client, remember=True):
    response = await client.post(
        "/v1/staff/login",
        headers=SESSION_HEADERS,
        data={
            "username": "session@example.com",
            "password": "fixture",
            "remember_session": str(remember).lower(),
        },
    )
    assert response.status_code == 200, response.text
    return response


@pytest.mark.asyncio
async def test_login_cookie_is_persistent_http_only_and_secure(browser_app):
    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await login(client)
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"]
        for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            assert attribute in cookie
        assert f"Max-Age={WEEK}" in cookie
        assert len(redis.values) == 1
        assert client.cookies.get("tgo-staff-session") not in next(
            iter(redis.values)
        )
        assert "refresh_token" not in response.json()


@pytest.mark.asyncio
async def test_activity_rolls_seven_days_beyond_original_login(browser_app):
    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        redis.now = WEEK - 1
        active = await client.post(
            "/v1/staff/session/refresh?active=true",
            headers=SESSION_HEADERS,
        )
        assert active.status_code == 200, active.text
        redis.now += WEEK - 1
        assert (
            await client.post(
                "/v1/staff/session/refresh",
                headers=SESSION_HEADERS,
            )
        ).status_code == 200
        redis.now += 1
        assert (
            await client.post(
                "/v1/staff/session/refresh?active=true",
                headers=SESSION_HEADERS,
            )
        ).status_code == 401
        assert not redis.values


@pytest.mark.asyncio
async def test_background_refresh_never_extends_idle_deadline(browser_app):
    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        redis.now = WEEK - 10
        response = await client.post(
            "/v1/staff/session/refresh",
            headers=SESSION_HEADERS,
        )
        assert response.status_code == 200, response.text
        assert response.json()["expires_in"] <= 10
        redis.now = WEEK
        assert (
            await client.post(
                "/v1/staff/session/refresh",
                headers=SESSION_HEADERS,
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_even_copied_session_cookie(browser_app):
    app, _, _, _, _ = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        cookie = client.cookies.get("tgo-staff-session")
        response = await client.post(
            "/v1/staff/session/logout",
            headers=SESSION_HEADERS,
        )
        assert response.status_code == 204
        client.cookies.set("tgo-staff-session", cookie)
        assert (
            await client.post(
                "/v1/staff/session/refresh",
                headers=SESSION_HEADERS,
            )
        ).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable", "password", "staff", "project"])
async def test_account_changes_block_session_renewal(browser_app, change):
    app, db, member, project, _ = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        if change == "disable":
            member.account_enabled = False
        elif change == "password":
            member.token_version += 1
        elif change == "staff":
            member.deleted_at = member.created_at
        else:
            project.deleted_at = project.created_at
        db.commit()
        assert (
            await client.post(
                "/v1/staff/session/refresh?active=true",
                headers=SESSION_HEADERS,
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_valid_access_token_is_reused_without_chat_reconnect(
    browser_app,
):
    app, _, _, _, _ = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        token = (await login(client)).json()["access_token"]
        endpoints.wukongim_client.register_or_login_user.reset_mock()
        response = await client.post(
            "/v1/staff/session/refresh?active=true",
            headers={**SESSION_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["access_token"] == token
        endpoints.wukongim_client.register_or_login_user.assert_not_called()


@pytest.mark.asyncio
async def test_expired_access_token_can_be_restored_from_session(browser_app):
    app, _, member, _, _ = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        old = security.create_access_token(
            member.username,
            member.project_id,
            expires_delta=timedelta(seconds=-1),
        )
        response = await client.post(
            "/v1/staff/session/refresh?active=true",
            headers={**SESSION_HEADERS, "Authorization": f"Bearer {old}"},
        )
        assert response.status_code == 200, response.text
        assert (
            security.verify_token(response.json()["access_token"]) is not None
        )
        endpoints.wukongim_client.register_or_login_user.assert_awaited()


@pytest.mark.asyncio
async def test_cli_login_contract_and_operator_session_are_unchanged(
    browser_app,
):
    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await login(client, remember=False)
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        assert not redis.values
        assert (
            await client.post(
                "/v1/staff/session/refresh",
                headers=SESSION_HEADERS,
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_cross_site_session_mutations_are_rejected(browser_app):
    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        deadline = next(iter(redis.values.values()))[1]
        for headers in (
            {},
            {
                **SESSION_HEADERS,
                "Origin": "https://evil.test",
                "Sec-Fetch-Site": "cross-site",
            },
        ):
            response = await client.post(
                "/v1/staff/session/refresh?active=true",
                headers=headers,
            )
            assert response.status_code == 403
        assert next(iter(redis.values.values()))[1] == deadline


@pytest.mark.asyncio
async def test_browser_access_never_outlives_seven_day_session(
    browser_app, monkeypatch
):
    app, _, _, _, _ = browser_app
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 30)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        assert (await login(client)).json()["expires_in"] == WEEK


@pytest.mark.asyncio
async def test_redis_outage_returns_retryable_error_not_invalid_login(
    browser_app, monkeypatch
):
    from redis.exceptions import ConnectionError
    from app.services import staff_browser_session

    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        previous = client.cookies.get("tgo-staff-session")
        monkeypatch.setattr(
            redis, "eval", AsyncMock(side_effect=ConnectionError())
        )
        response = await client.post(
            "/v1/staff/session/refresh",
            headers=SESSION_HEADERS,
        )
        assert response.status_code == 503
        assert (
            client.cookies.get(staff_browser_session.COOKIE_NAME) == previous
        )


@pytest.mark.asyncio
async def test_new_browser_login_replaces_old_refresh_cookie(browser_app):
    app, _, _, _, redis = browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        await login(client)
        previous = client.cookies.get("tgo-staff-session")
        await login(client)
        assert len(redis.values) == 1
        assert client.cookies.get("tgo-staff-session") != previous
        client.cookies.clear()
        client.cookies.set("tgo-staff-session", previous)
        assert (
            await client.post(
                "/v1/staff/session/refresh",
                headers=SESSION_HEADERS,
            )
        ).status_code == 401
