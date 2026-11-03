"""Synthetic providers validate real ownership/audit boundaries, not live merchants."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.config import settings
from app.api.business_trials import router, get_runner
from app.domain.business_tools.trial_runner import BusinessTrialRunner
from app.domain.business_tools.customer_tools import (
    CustomerOwnership,
    CustomerProfileResult,
    MembershipResult,
    OwnedCustomerProfile,
    OwnedMembership,
)
from app.domain.business_tools.models import (
    OwnedOrderQueryResult,
    OwnedLogisticsQueryResult,
    OrderOwnership,
    OrderQueryResult,
    LogisticsQueryResult,
)
from app.domain.business_tools.providers import BusinessProviderResultError
from app.schemas.agent_business_trials import (
    BusinessTrialCommand,
    BusinessTrialCheck,
    BusinessTrialAuthority,
    BusinessTrialCustomerEvidence,
    BusinessTrialRequest,
)
from app.schemas.business_trial_resolution import BusinessTrialResolution
from app.services.business_trial_owner_client import BusinessTrialOwnerClient
from app.services.business_connection_client import ConnectionAuthorityUnavailable
from test_business_connection_execution import snapshot


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("fixture-only-business-service-key-32-characters"),
    )
    command = BusinessTrialCommand(
        project_id=uuid4(),
        actor_id=uuid4(),
        agent_id=uuid4(),
        connection_id=uuid4(),
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
        visitor_id=uuid4(),
        order_no=SecretStr("PRIVATE-ORDER-1234"),
        customer_session=SecretStr("private-session-do-not-store"),
    )
    source = snapshot(command, mode="http")
    source.connection.enabled = True
    customer = BusinessTrialCustomerEvidence(
        binding_id=uuid4(),
        binding_revision=1,
        policy_revision=1,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    def resolve(check):
        return BusinessTrialResolution(
            snapshot=source,
            customer_id=SecretStr("verified-merchant-customer"),
            customer_evidence=customer,
            authority=BusinessTrialAuthority(
                **command.identity(),
                phase=check.phase,
                checked_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=2),
            ),
        )

    owner, audit, provider = AsyncMock(), AsyncMock(), AsyncMock()
    owner.resolve.side_effect = resolve

    def order(query, context):
        assert (
            context.external_customer_id == "verified-merchant-customer"
            and context.visitor_id == str(command.visitor_id)
        )
        return OwnedOrderQueryResult(
            ownership=OrderOwnership(
                tenant_id=context.tenant_id,
                visitor_id=context.visitor_id,
                order_no=query.order_no,
            ),
            result=OrderQueryResult(
                order_no=query.order_no,
                status="已发货",
                amount_minor=12300,
                currency="CNY",
            ),
        )

    provider.query_order.side_effect = order
    runner = BusinessTrialRunner(owner, audit=audit)
    runner.provider = lambda value: provider
    return command, resolve, owner, audit, provider, runner


@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
def test_customer_trial_requires_verified_session_but_no_order_number(
    fixture, operation
):
    command, *_ = fixture
    payload = command.model_dump(mode="json")
    payload.update(operation=operation, order_no=None)
    customer_trial = BusinessTrialCommand.model_validate(payload)
    assert "order_no" not in customer_trial.private_payload()
    assert (
        BusinessTrialRequest.model_validate(
            customer_trial.request().model_dump(mode="json")
        ).operation
        == operation
    )
    with pytest.raises(ValueError):
        BusinessTrialCommand.model_validate({**payload, "order_no": "ORDER-TEST-001"})


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_customer_trial_uses_verified_identity_and_audit(
    fixture, operation, foreign
):
    command, _, owner, audit, _, runner = fixture
    command = BusinessTrialCommand.model_validate(
        {**command.model_dump(mode="json"), "operation": operation, "order_no": None}
    )
    source = fixture[1](
        BusinessTrialCheck(command=fixture[0], phase="before_query")
    ).snapshot
    source.connection.capabilities = [operation]
    source.connection.configuration.customer_profile_path = "/profile"
    source.connection.configuration.membership_path = "/membership"

    def resolve(check):
        original = fixture[1](BusinessTrialCheck(command=fixture[0], phase=check.phase))
        return original.model_copy(
            update={
                "snapshot": source,
                "authority": original.authority.model_copy(
                    update={"operation": operation}
                ),
            }
        )

    owner.resolve.side_effect = resolve
    customer_provider = AsyncMock()

    def ownership(context):
        assert context.external_customer_id == "verified-merchant-customer"
        return CustomerOwnership(
            tenant_id=context.tenant_id,
            visitor_id="another-visitor" if foreign else context.visitor_id,
            external_customer_id=context.external_customer_id,
        )

    customer_provider.query_profile.side_effect = lambda context: OwnedCustomerProfile(
        ownership=ownership(context),
        result=CustomerProfileResult(segment="VIP", service_tags=["常客"]),
    )
    customer_provider.query_membership.side_effect = lambda context: OwnedMembership(
        ownership=ownership(context),
        result=MembershipResult(tier="金卡", points_balance=120),
    )
    runner.customer_provider = lambda value: customer_provider
    result = await runner.run(command)
    assert result.outcome == ("failed" if foreign else "passed")
    assert result.error_code == ("denied" if foreign else None)
    if not foreign:
        assert result.summary.status == "查询完成"
        assert (result.summary.segment == "VIP") == (
            operation == "customer_profile_query"
        )
        assert (result.summary.tier == "金卡") == (operation == "membership_query")
    assert [call.args[0].phase for call in owner.resolve.await_args_list] == [
        "before_query",
        "after_query",
    ]
    audit.record.assert_awaited_once()
    assert "verified-merchant-customer" not in result.model_dump_json()


def test_customer_trial_never_records_identity_as_business_label(fixture):
    command, resolve, *_ = fixture
    resolution = resolve(BusinessTrialCheck(command=command, phase="before_query"))
    assert (
        BusinessTrialRunner.safe_customer_label(
            "verified-merchant-customer", command, resolution
        )
        == "结果标签需配置映射"
    )
    assert (
        BusinessTrialRunner.safe_customer_label(
            str(command.visitor_id), command, resolution
        )
        == "结果标签需配置映射"
    )


@pytest.mark.asyncio
async def test_employee_test_uses_verified_customer_audited_query_and_two_current_checks(
    fixture,
):
    command, resolve, owner, audit, provider, runner = fixture
    result = await runner.run(command)
    assert (
        result.outcome == "passed"
        and not result.can_enable
        and result.summary.amount_minor == 12300
    )
    assert [call.args[0].phase for call in owner.resolve.await_args_list] == [
        "before_query",
        "after_query",
    ]
    audit.record.assert_awaited_once()
    event = audit.record.await_args.args[0]
    assert event.request_id == str(
        command.request_id
    ) and event.conversation_id == "business-test-" + str(command.report_id)
    assert (
        command.order_no.get_secret_value()
        not in result.model_dump_json() + event.model_dump_json()
    )
    assert (
        "verified-merchant-customer"
        not in result.model_dump_json() + event.model_dump_json()
    )


@pytest.mark.asyncio
async def test_other_visitors_order_is_denied_and_audited(fixture):
    command, resolve, owner, audit, provider, runner = fixture
    original = provider.query_order.side_effect

    def foreign(query, context):
        result = original(query, context)
        result.ownership.visitor_id = "somebody-else"
        return result

    provider.query_order.side_effect = foreign
    result = await runner.run(command)
    assert (
        result.outcome == "failed"
        and result.error_code == "denied"
        and result.summary is None
    )
    assert audit.record.await_args.args[0].outcome == "denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["not_found", "not_shipped", "authentication_failed", "business_failed"]
)
async def test_business_failures_never_turn_into_http_success_proof(fixture, code):
    command, resolve, owner, audit, provider, runner = fixture
    provider.query_order.side_effect = BusinessProviderResultError(code)
    result = await runner.run(command)
    assert (
        result.error_code == code
        and result.outcome == "failed"
        and result.summary is None
    )


@pytest.mark.asyncio
async def test_audit_failure_discards_result(fixture):
    command, resolve, owner, audit, provider, runner = fixture
    audit.record.side_effect = RuntimeError("private audit error")
    result = await runner.run(command)
    assert result.error_code == "audit_unavailable" and result.summary is None


@pytest.mark.asyncio
async def test_identity_changed_after_query_discards_receipt(fixture):
    command, resolve, owner, audit, provider, runner = fixture

    def changed(check):
        value = resolve(check)
        if check.phase == "after_query":
            value.customer_evidence = value.customer_evidence.model_copy(
                update={"binding_revision": 2}
            )
        return value

    owner.resolve.side_effect = changed
    with pytest.raises(ConnectionAuthorityUnavailable):
        await runner.run(command)


@pytest.mark.asyncio
async def test_logistics_is_independent_typed_operation(fixture):
    command, resolve, owner, audit, provider, runner = fixture
    command = command.model_copy(update={"operation": "logistics_query"})

    def logistics(query, context):
        return OwnedLogisticsQueryResult(
            ownership=OrderOwnership(
                tenant_id=context.tenant_id,
                visitor_id=context.visitor_id,
                order_no=query.order_no,
            ),
            result=LogisticsQueryResult(
                order_no=query.order_no,
                status="运输中",
                carrier="顺丰",
                tracking_no_masked="SF****1234",
            ),
        )

    provider.query_logistics.side_effect = logistics
    result = await runner.run(command)
    assert (
        result.operation == "logistics_query"
        and result.summary.tracking_no_masked == "SF****1234"
    )
    provider.query_order.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "phase",
        "actor",
        "mode",
        "disabled",
        "missing_scope",
        "expired",
        "large",
        "customer",
    ],
)
async def test_owner_rejects_unconfirmed_private_scope(fixture, bad):
    command, resolve, owner, audit, provider, runner = fixture
    check = BusinessTrialCheck(command=command, phase="before_query")
    value = resolve(check).model_dump(mode="json")
    if bad == "phase":
        value["authority"]["phase"] = "after_query"
    if bad == "actor":
        value["authority"]["actor_id"] = str(uuid4())
    if bad == "mode":
        value["snapshot"]["connection"]["mode"] = "demo"
    if bad == "disabled":
        value["snapshot"]["connection"]["enabled"] = False
    if bad == "missing_scope":
        del value["authority"]["can_query_test"]
    if bad == "expired":
        value["customer_evidence"]["expires_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    if bad == "customer":
        del value["customer_id"]

    def respond(request):
        assert json.loads(request.content) == check.private_payload()
        return (
            httpx.Response(200, content="x" * 65537)
            if bad == "large"
            else httpx.Response(200, json=value)
        )

    with pytest.raises(ConnectionAuthorityUnavailable):
        await BusinessTrialOwnerClient(transport=httpx.MockTransport(respond)).resolve(
            check
        )


@pytest.mark.asyncio
async def test_private_route_rejects_other_service_identity_and_redacts_bad_input(
    fixture,
):
    command, resolve, owner, audit, provider, runner = fixture
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_runner] = lambda: runner
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/business/trials/query",
            json=command.private_payload(),
            headers={"X-Internal-API-Key": "wrong-purpose"},
        )
        assert response.status_code == 401
        payload = command.private_payload()
        payload["customer_id"] = "private-self-declared"
        response = await client.post(
            "/business/trials/query",
            json=payload,
            headers={
                "X-Business-Service-Token": settings.BUSINESS_CONNECTION_SERVICE_TOKEN.get_secret_value()
            },
        )
        assert (
            response.status_code == 422
            and "private-self-declared" not in response.text
            and "private-session" not in response.text
        )


def test_private_identifiers_and_credentials_are_removed_from_summary(fixture):
    command, resolve, owner, audit, provider, runner = fixture
    value = resolve(BusinessTrialCheck(command=command, phase="before_query"))
    summary = runner.safe_text(
        "PRIVATE-ORDER-1234 verified-merchant-customer test-only-upstream-credential user@example.test 13648024940",
        command,
        value,
    )
    assert all(
        secret not in summary
        for secret in [
            "PRIVATE-ORDER-1234",
            "verified-merchant-customer",
            "test-only-upstream-credential",
            "user@example.test",
            "13648024940",
        ]
    )


@pytest.mark.asyncio
async def test_upstream_status_text_cannot_smuggle_personal_details_into_report(
    fixture,
):
    command, resolve, owner, audit, provider, runner = fixture
    original = provider.query_order.side_effect

    def private_status(query, context):
        result = original(query, context)
        result.result.status = "收货人测试姓名，测试省测试市测试街道，user@example.test"
        return result

    provider.query_order.side_effect = private_status
    result = await runner.run(command)
    assert result.summary.status == "结果标签需配置映射"
    assert (
        "测试街道" not in result.model_dump_json()
        and "user@example.test" not in result.model_dump_json()
    )
