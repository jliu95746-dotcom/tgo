"""Private purpose requires actual signed customer sessions; mocks only transport."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr

from app.api.internal.endpoints import agent_business_trials as routes
from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.agent_business_trials import (
    BusinessTrialCommand,
    BusinessTrialCheck,
    BusinessTrialAuthority,
    BusinessTrialReceipt,
    BusinessTrialCustomerEvidence,
)
from app.schemas.business_connections import (
    BusinessConnectionResponse,
    BusinessHTTPConfiguration,
)
from app.services import agent_business_trial_client, business_trial_query_client
from tests.test_business_customer_identity import setup, claims, sign


def command(project=None, connection=None, visitor=None, actor=None, session=None):
    return BusinessTrialCommand(
        project_id=project or uuid4(),
        actor_id=actor or uuid4(),
        agent_id=uuid4(),
        connection_id=connection or uuid4(),
        report_id=uuid4(),
        request_id=uuid4(),
        attempt_id=uuid4(),
        operation="order_query",
        binding_id=uuid4(),
        binding_revision=1,
        configuration_fingerprint="b" * 64,
        expected_revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        configuration_test_id=uuid4(),
        configuration_test_valid_until=datetime.now(UTC) + timedelta(minutes=10),
        visitor_id=visitor or uuid4(),
        order_no=SecretStr("PRIVATE-ORDER-1234"),
        customer_session=session or SecretStr("private-invalid-session"),
    )


@pytest.mark.asyncio
async def test_resolution_verifies_signed_customer_before_single_use_employee_grant(
    setup, monkeypatch
):
    service, db, project, connection, platform, visitor, actor, policy = setup
    session = service.exchange(connection, sign(claims(setup)))
    query = command(project, connection, visitor, actor, session.session_token)
    check = BusinessTrialCheck(command=query, phase="before_query")
    snapshot = BusinessConnectionResponse(
        id=connection,
        project_id=project,
        name="隔离商户",
        mode="http",
        capabilities=["order_query"],
        configuration=BusinessHTTPConfiguration(
            base_url="https://merchant.example.test", auth_mode="none"
        ),
        credentials_configured=False,
        enabled=True,
        revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        test_status="passed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(routes, "actor_check", Mock())
    monkeypatch.setattr(
        routes.business_connections, "resolve", Mock(return_value=(snapshot, None))
    )
    monkeypatch.setattr(
        routes, "BusinessCustomerIdentityService", lambda unused: service
    )
    grant = AsyncMock(
        return_value=BusinessTrialAuthority(
            **query.identity(),
            phase="before_query",
            checked_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )
    )
    monkeypatch.setattr(routes.agent_business_trial_client, "authorize", grant)
    result = await routes.resolve(connection, check, db)
    assert (
        result.customer_id.get_secret_value() == "merchant-member-private-001"
        and not result.can_enable
    )
    assert (
        result.customer_evidence.binding_id == session.binding_id
        and result.authority.can_query_test
    )
    grant.assert_awaited_once_with(routes.ai_client, check)
    grant.reset_mock()
    with pytest.raises(TGOAPIException):
        await routes.resolve(
            connection,
            BusinessTrialCheck(
                command=query.model_copy(
                    update={"customer_session": SecretStr("forged-session-token")}
                ),
                phase="before_query",
            ),
            db,
        )
    grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_customer_during_employee_grant_never_releases_private_snapshot(
    setup, monkeypatch
):
    service, db, project, connection, platform, visitor, actor, policy = setup
    session = service.exchange(connection, sign(claims(setup)))
    query = command(project, connection, visitor, actor, session.session_token)
    snapshot = Mock(
        enabled=True,
        mode="http",
        capabilities=["order_query"],
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
    )
    monkeypatch.setattr(routes, "actor_check", Mock())
    monkeypatch.setattr(
        routes.business_connections, "resolve", Mock(return_value=(snapshot, None))
    )
    monkeypatch.setattr(
        routes, "BusinessCustomerIdentityService", lambda unused: service
    )

    async def revoke(client, check):
        from app.models.business_customer_identity import BusinessCustomerBinding

        row = db.get(BusinessCustomerBinding, session.binding_id)
        row.revoked_at = datetime.now(UTC)
        db.commit()
        return BusinessTrialAuthority(
            **query.identity(),
            phase=check.phase,
            checked_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )

    monkeypatch.setattr(routes.agent_business_trial_client, "authorize", revoke)
    with pytest.raises(TGOAPIException):
        await routes.resolve(
            connection, BusinessTrialCheck(command=query, phase="before_query"), db
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["connection", "visitor", "actor", "test", "phase", "missing", "time"]
)
async def test_employee_grant_response_must_match_original_command(change):
    query = command()
    check = BusinessTrialCheck(command=query, phase="before_query")
    body = BusinessTrialAuthority(
        **query.identity(),
        phase=check.phase,
        checked_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    ).model_dump(mode="json")
    if change in {"connection", "actor"}:
        body[change + "_id"] = str(uuid4())
    if change == "visitor":
        body["customer_id"] = "self-declared"
    if change == "test":
        body["configuration_test_id"] = str(uuid4())
    if change == "phase":
        body["phase"] = "after_query"
    if change == "missing":
        del body["can_query_test"]
    if change == "time":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()

    class Client:
        def _trial_headers(self, p, a):
            return {"project": str(p), "actor": str(a)}

        async def _make_request(self, method, path, **kwargs):
            assert kwargs["json_data"] == check.private_payload()
            return httpx.Response(200, json=body)

    with pytest.raises(HTTPException):
        await agent_business_trial_client.authorize(Client(), check)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["valid", "report", "purpose", "missing", "large", "expired_customer", "error"],
)
async def test_adapter_receipt_is_typed_bounded_and_never_echoes_upstream(
    change, monkeypatch
):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("fixture-only-service-token-32-characters"),
    )
    query = command()
    result = BusinessTrialReceipt(
        **query.identity(),
        outcome="passed",
        summary={"status": "已发货"},
        error_code=None,
        checked_at=datetime.now(UTC),
        customer_evidence=BusinessTrialCustomerEvidence(
            binding_id=uuid4(),
            binding_revision=1,
            policy_revision=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
    )
    body = result.model_dump(mode="json")
    if change == "report":
        body["report_id"] = str(uuid4())
    if change == "purpose":
        body["scope"] = "business_connection_test"
    if change == "missing":
        del body["can_enable"]
    if change == "expired_customer":
        body["customer_evidence"]["expires_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()

    def respond(request):
        import json

        assert json.loads(request.content) == query.private_payload()
        if change == "large":
            return httpx.Response(200, content="x" * 65537)
        if change == "error":
            return httpx.Response(500, text="private-upstream-secret")
        return httpx.Response(200, json=body)

    if change == "valid":
        assert (
            await business_trial_query_client.query(
                query, transport=httpx.MockTransport(respond)
            )
        ).report_id == query.report_id
    else:
        with pytest.raises(TGOAPIException) as error:
            await business_trial_query_client.query(
                query, transport=httpx.MockTransport(respond)
            )
        assert "private-upstream-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_private_routes_do_not_accept_staff_auth_or_leak_invalid_inputs(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("fixture-only-service-token-32-characters"),
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/internal")
    body = BusinessTrialCheck(command=command(), phase="before_query").private_payload()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/internal/business-connections/{body['command']['connection_id']}/business-trial",
            json=body,
        )
        assert response.status_code == 403
        body["command"]["customer_id"] = "private-self-declared"
        response = await client.post(
            f"/internal/business-connections/{body['command']['connection_id']}/business-trial",
            json=body,
            headers={
                "X-Business-Service-Token": settings.BUSINESS_CONNECTION_SERVICE_TOKEN.get_secret_value()
            },
        )
        assert (
            response.status_code == 422
            and "private-self-declared" not in response.text
            and "PRIVATE-ORDER" not in response.text
        )
