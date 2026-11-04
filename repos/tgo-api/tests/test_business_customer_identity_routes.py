"""Public exchange needs a merchant signature, while management needs an admin."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.v1.endpoints.business_customer_identity import router, get_service
from app.core.config import settings
from app.core.exceptions import (
    TGOAPIException,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.security import get_current_active_user
from tests.test_business_customer_identity import (
    setup as identity_setup_fixture,
    claims,
    sign,
    KEY,
    VIEWER_SECRET,
)

setup = identity_setup_fixture


@pytest.fixture
def api(setup, monkeypatch):
    service, _, project, _, _, _, actor, _ = setup
    staff = SimpleNamespace(
        id=actor,
        project_id=project,
        username="identity-fixture-admin",
        role="admin",
        account_enabled=True,
        deleted_at=None,
    )
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    app = FastAPI()
    app.include_router(router, prefix="/v1/business-connections")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_current_active_user] = lambda: staff
    return app, service, staff


@pytest.mark.asyncio
async def test_public_signed_exchange_and_admin_revocation(api, setup):
    app, service, _ = api
    connection = setup[3]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/v1/business-connections/" + str(connection)
        policy = await client.get(path + "/customer-identity-policy")
        assert (
            policy.status_code == 200
            and KEY not in policy.text
            and "signing_ciphertext" not in policy.text
        )
        proof = sign(claims(setup)).get_secret_value()
        exchanged = await client.post(
            path + "/customer-identities/exchange",
            json={
                "merchant_assertion": proof,
                "visitor_access_token": VIEWER_SECRET.get_secret_value(),
            },
        )
        assert exchanged.status_code == 200 and exchanged.json()["can_enable"] is False
        assert exchanged.headers["Cache-Control"] == "no-store"
        assert (
            "merchant-member-private-001" not in exchanged.text
            and proof not in exchanged.text
        )
        binding = exchanged.json()["binding_id"]
        reports = await client.get(path + "/customer-identities")
        assert reports.status_code == 200 and reports.json()[0]["status"] == "current"
        assert "session_token" not in reports.text and "customer_id" not in reports.text
        revoked = await client.post(
            path + "/customer-identities/" + binding + "/revoke",
            json={"expected_revision": 1},
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
        assert (
            await client.post(
                path + "/customer-identities/exchange",
                json={"merchant_assertion": proof},
            )
        ).status_code == 409


@pytest.mark.asyncio
async def test_non_admin_cannot_read_policy_bindings_or_rotate_key(api, setup):
    app, _, staff = api
    staff.role = "agent"
    path = "/v1/business-connections/" + str(setup[3])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(path + "/customer-identity-policy")).status_code == 403
        assert (await client.get(path + "/customer-identities")).status_code == 403
        assert (
            await client.put(
                path + "/customer-identity-policy",
                json={"expected_revision": 1, "platform_ids": [str(setup[4])]},
            )
        ).status_code == 403


@pytest.mark.asyncio
async def test_errors_never_echo_unparsed_assertion_or_signer_secret(api, setup):
    app, _, _ = api
    path = "/v1/business-connections/" + str(setup[3])
    secret = "unparsed-secret-value-to-redact"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        malformed = await client.post(
            path + "/customer-identities/exchange",
            json={"merchant_assertion": {"secret": secret}},
        )
        assert malformed.status_code == 422 and secret not in malformed.text
        unauthorized = await client.post(
            path + "/customer-identities/exchange", json={"merchant_assertion": secret}
        )
        assert unauthorized.status_code == 403 and secret not in unauthorized.text
        badkey = await client.put(
            path + "/customer-identity-policy",
            json={
                "expected_revision": 1,
                "platform_ids": [str(setup[4])],
                "signing_secret": secret,
            },
        )
        assert badkey.status_code == 422 and secret not in badkey.text
