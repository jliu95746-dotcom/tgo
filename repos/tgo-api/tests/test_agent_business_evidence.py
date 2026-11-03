"""Historical customer proof does not mint/renew an execution session."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.business_customer_identity import BusinessCustomerBinding
from app.schemas.agent_business_reviews import BusinessTestEvidenceQuery
from app.services.agent_business_evidence import current_evidence
from tests.test_business_customer_identity import setup, claims, sign


def query(setup):
    service, db, project, connection, platform, visitor, actor, policy = setup
    session = service.exchange(connection, sign(claims(setup)))
    return BusinessTestEvidenceQuery(
        project_id=project,
        requested_by=actor,
        connection_id=connection,
        configuration_revision=1,
        customer_evidence={
            "binding_id": session.binding_id,
            "binding_revision": 1,
            "policy_revision": policy.revision,
            "expires_at": session.expires_at,
        },
        observed_at=datetime.now(UTC),
    )


def test_signed_binding_evidence_is_current_without_customer_secret_or_new_session(
    setup,
):
    service, db, *_ = setup
    result = current_evidence(db, query(setup), identity=service)
    assert result.status == "current" and not result.can_enable
    assert (
        not {"customer_id", "session_token", "credentials"} & result.model_dump().keys()
    )
    assert "merchant-member-private-001" not in result.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        "binding",
        "project",
        "config",
        "policy",
        "revision",
        "expiry",
        "revoke",
        "scope",
        "cipher",
    ],
)
def test_changed_customer_evidence_never_returns_current(setup, change):
    service, db, *_ = setup
    request = query(setup)
    row = db.get(BusinessCustomerBinding, request.customer_evidence.binding_id)
    if change == "binding":
        request = request.model_copy(
            update={
                "customer_evidence": request.customer_evidence.model_copy(
                    update={"binding_id": uuid4()}
                )
            }
        )
    if change == "project":
        row.project_id = uuid4()
    if change == "config":
        request = request.model_copy(update={"configuration_revision": 2})
    if change == "policy":
        request = request.model_copy(
            update={
                "customer_evidence": request.customer_evidence.model_copy(
                    update={"policy_revision": 2}
                )
            }
        )
    if change == "revision":
        row.revision = 2
    if change == "expiry":
        row.expires_at = row.expires_at + timedelta(seconds=1)
    if change == "revoke":
        row.revoked_at = datetime.now(UTC)
    if change == "scope":
        service.platform_reader = lambda p, x: False
    if change == "cipher":
        row.customer_ciphertext = "invalid-private-ciphertext"
    db.commit()
    result = current_evidence(db, request, identity=service)
    assert result.status == "changed" and result.issues and not result.can_enable


def test_customer_session_expiry_does_not_rewrite_historic_business_evidence(
    setup, monkeypatch
):
    service, db, *_ = setup
    request = query(setup)
    from app.services import agent_business_evidence

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=2)

    monkeypatch.setattr(agent_business_evidence, "datetime", Later)
    result = current_evidence(db, request, identity=service)
    assert (
        result.status == "current"
        and result.checked_at > request.customer_evidence.expires_at
    )
    # No session is renewed; the evidence retains the original expiration.
    assert result.customer_evidence.expires_at == request.customer_evidence.expires_at


@pytest.mark.asyncio
async def test_customer_exchange_validation_never_echoes_signed_input():
    import httpx
    from fastapi import FastAPI
    from app.api.v1.endpoints.business_customer_identity import router

    app = FastAPI()
    app.include_router(router, prefix="/business-connections")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/business-connections/{uuid4()}/customer-identities/exchange",
            json={
                "merchant_assertion": "private-signed-customer-input",
                "credentials": {"token": "private-input-token"},
            },
        )
        assert (
            response.status_code == 422
            and "private-signed-customer-input" not in response.text
            and "private-input-token" not in response.text
        )
