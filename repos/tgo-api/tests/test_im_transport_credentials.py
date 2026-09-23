"""Business sessions must not race for the private IM user's credential."""

from secrets import token_urlsafe
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services.im_credentials import transport_token
from app.services.wukongim_client import WuKongIMClient


def test_transport_credentials_are_stable_private_and_identity_bound(
    monkeypatch,
):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SECRET_KEY", token_urlsafe(40))
    first = transport_token("one-staff", "browser-session-one")
    assert first == transport_token("one-staff", "browser-session-two")
    assert len(first) == 64
    assert first != "browser-session-one"
    assert first != transport_token("two-staff", "browser-session-one")
    monkeypatch.setattr(settings, "SECRET_KEY", token_urlsafe(40))
    assert first != transport_token("one-staff", "browser-session-one")


def test_legacy_transport_keeps_supplied_token(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    assert transport_token("one-staff", "legacy-token") == "legacy-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["staff", "vtr"])
async def test_registration_uses_same_private_credential_as_gateway(
    monkeypatch,
    suffix,
):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    client = WuKongIMClient()
    client.enabled = True
    request = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_make_request", request)
    uid = f"synthetic-{suffix}"
    await client.register_or_login_user(uid=uid, token="first-browser-session")
    first = request.call_args.kwargs["json_data"]["token"]
    await client.register_or_login_user(
        uid=uid, token="second-browser-session"
    )
    second = request.call_args.kwargs["json_data"]["token"]
    assert first == second == transport_token(uid, "any-valid-browser-session")
    assert first != "first-browser-session"


@pytest.mark.asyncio
@pytest.mark.parametrize("saas_enabled", [False, True])
@pytest.mark.parametrize("device_level", [None, 0, 1])
async def test_saas_registration_allows_parallel_browser_sessions(
    monkeypatch, saas_enabled, device_level
):
    monkeypatch.setattr(settings, "SAAS_ENABLED", saas_enabled)
    monkeypatch.setattr(settings, "WUKONGIM_DEVICE_LEVEL", 1)
    client = WuKongIMClient()
    client.enabled = True
    request = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_make_request", request)
    await client.register_or_login_user(
        uid="synthetic-staff", token="browser-session",
        device_level=device_level,
    )
    expected = 0 if saas_enabled else (
        1 if device_level is None else device_level
    )
    assert request.call_args.kwargs["json_data"]["device_level"] == expected
