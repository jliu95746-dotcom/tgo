"""Formal queries enforce real session ownership, audit, and unchanged original authority."""

from hashlib import sha256
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.domain.business_tools.execution_runner import BusinessExecutionRunner
from app.domain.business_tools.customer_tools import (
    CustomerOwnership,
    CustomerProfileResult,
    MembershipResult,
    OwnedCustomerProfile,
    OwnedMembership,
)
from app.domain.business_tools.models import (
    OwnedOrderQueryResult,
    OrderOwnership,
    OrderQueryResult,
)
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionCheck,
    BusinessExecutionAuthority,
)
from app.schemas.business_execution_context import BusinessRequestPrincipal
from app.schemas.business_execution_resolution import BusinessExecutionResolution
from app.services.business_connection_client import ConnectionAuthorityUnavailable
from app.services.business_execution_owner_client import BusinessExecutionOwnerClient
from test_business_connection_execution import snapshot


@pytest.fixture
def execution(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        SecretStr("formal-test-service-token-at-least-32-chars"),
    )
    project, agent, visitor, request, connection = [uuid4() for _ in range(5)]
    command = BusinessExecutionCommand(
        query_id=uuid4(),
        connection_id=connection,
        binding_id=uuid4(),
        binding_revision=1,
        operation="order_query",
        context_digest="d" * 64,
        order_no=SecretStr("PRIVATE-ORDER-001"),
        reference={
            "project_id": project,
            "agent_id": agent,
            "channel": "web",
            "configuration_fingerprint": "a" * 64,
            "policy_revision": "b" * 64,
            "business_evidence_revision": "c" * 64,
        },
        context={
            "project_id": project,
            "visitor_id": visitor,
            "channel": "web",
            "request_id": request,
            "request_fingerprint": "e" * 64,
            "context_token": "private-context",
        },
    )
    expires = datetime.now(UTC) + timedelta(minutes=2)
    principal = BusinessRequestPrincipal(
        **command.context.model_dump(),
        platform_id=uuid4(),
        visitor_session_id=uuid4(),
        source_message_id="source-message",
        response_client_msg_no="original-attempt",
        context_digest=command.context_digest,
        bindings=[
            {
                "connection_id": connection,
                "binding_id": uuid4(),
                "binding_revision": 1,
                "policy_revision": 1,
                "connection_configuration_revision": 1,
                "expires_at": expires,
            }
        ],
        checked_at=datetime.now(UTC),
        expires_at=expires,
    )
    source = snapshot(
        SimpleNamespace(connection_id=connection, project_id=project), mode="http"
    )
    source.connection.enabled = True
    authority = BusinessExecutionAuthority(
        **command.identity(),
        actor_id=uuid4(),
        phase="before_query",
        connection_revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        report_id=uuid4(),
        review_id=uuid4(),
        configuration_test_id=uuid4(),
        checked_at=datetime.now(UTC),
        expires_at=expires,
    )

    def resolve(check):
        return BusinessExecutionResolution(
            snapshot=source,
            principal=principal,
            customer_id=SecretStr("merchant-customer"),
            authority=authority.model_copy(
                update={"phase": check.phase, "checked_at": datetime.now(UTC)}
            ),
        )

    owner, audit, provider = AsyncMock(), AsyncMock(), AsyncMock()
    owner.resolve.side_effect = resolve

    def order(query, context):
        assert (
            context.external_customer_id == "merchant-customer"
            and context.conversation_id == str(principal.visitor_session_id)
        )
        assert context.actor_id == str(agent) and context.request_id == str(
            command.query_id
        )
        return OwnedOrderQueryResult(
            ownership=OrderOwnership(
                tenant_id=project, visitor_id=str(visitor), order_no=query.order_no
            ),
            result=OrderQueryResult(
                order_no=query.order_no,
                status="已发货",
                amount_minor=12300,
                currency="CNY",
            ),
        )

    provider.query_order.side_effect = order
    runner = BusinessExecutionRunner(owner, audit=audit)
    runner.provider = lambda value: provider
    return command, resolve, owner, audit, provider, runner


@pytest.mark.asyncio
async def test_formal_query_uses_server_customer_and_actual_session_with_two_checks(
    execution,
):
    command, _, owner, audit, _, runner = execution
    result = await runner.run(command)
    assert result.outcome == "passed" and result.summary.status == "已发货"
    assert [call.args[0].phase for call in owner.resolve.await_args_list] == [
        "before_query",
        "after_query",
    ]
    audit.record.assert_awaited_once()
    saved = audit.record.await_args.args[0]
    expected_source = sha256(
        f"{command.reference.project_id.hex}\0source-message".encode()
    ).hexdigest()
    expected_response = sha256(
        f"{command.reference.project_id.hex}\0original-attempt".encode()
    ).hexdigest()
    assert saved.source_message_digest == expected_source
    assert saved.response_message_digest == expected_response
    assert "source-message" not in saved.model_dump_json()
    assert "original-attempt" not in saved.model_dump_json()
    for private in [
        "PRIVATE-ORDER-001",
        "private-context",
        "merchant-customer",
        "test-only-upstream-credential",
    ]:
        assert (
            private
            not in result.model_dump_json()
            + audit.record.await_args.args[0].model_dump_json()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_formal_customer_query_uses_verified_session_and_audits(execution, operation, foreign):
    command, resolve, owner, audit, _, runner = execution
    command = BusinessExecutionCommand.model_validate({
        **command.private_payload(), "operation": operation, "order_no": None,
    })
    source = resolve(BusinessExecutionCheck(command=execution[0], phase="before_query")).snapshot
    source.connection.capabilities = [operation]
    source.connection.configuration.customer_profile_path = "/profile"
    source.connection.configuration.membership_path = "/membership"

    def current(check):
        original = resolve(BusinessExecutionCheck(command=execution[0], phase=check.phase))
        return original.model_copy(update={
            "snapshot": source,
            "authority": original.authority.model_copy(update={"operation": operation}),
        })

    owner.resolve.side_effect = current
    provider = AsyncMock()

    def ownership(context):
        assert context.external_customer_id == "merchant-customer"
        assert context.request_id == str(command.query_id)
        return CustomerOwnership(
            tenant_id=context.tenant_id,
            visitor_id="other-visitor" if foreign else context.visitor_id,
            external_customer_id=context.external_customer_id,
        )

    provider.query_profile.side_effect = lambda context: OwnedCustomerProfile(
        ownership=ownership(context),
        result=CustomerProfileResult(segment="VIP", service_tags=["常客"]),
    )
    provider.query_membership.side_effect = lambda context: OwnedMembership(
        ownership=ownership(context),
        result=MembershipResult(tier="金卡", points_balance=120),
    )
    runner.customer_provider = lambda value: provider
    result = await runner.run(command)
    assert result.outcome == ("failed" if foreign else "passed")
    assert result.error_code == ("denied" if foreign else None)
    if not foreign:
        assert result.summary.status == "查询完成"
        assert (result.summary.segment == "VIP") == (operation == "customer_profile_query")
        assert (result.summary.tier == "金卡") == (operation == "membership_query")
    assert [call.args[0].phase for call in owner.resolve.await_args_list] == [
        "before_query", "after_query"
    ]
    audit.record.assert_awaited_once()
    assert "merchant-customer" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_foreign_order_is_denied_and_failed_receipt_contains_no_summary(
    execution,
):
    command, _, _, audit, provider, runner = execution
    original = provider.query_order.side_effect

    def foreign(query, context):
        value = original(query, context)
        value.ownership.visitor_id = "foreign-customer"
        return value

    provider.query_order.side_effect = foreign
    result = await runner.run(command)
    assert result.error_code == "denied" and result.summary is None
    assert audit.record.await_args.args[0].outcome == "denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["binding", "review", "customer", "session", "connection"]
)
async def test_after_query_change_discards_original_result(execution, fault):
    command, resolve, owner, _, _, runner = execution

    def changed(check):
        value = resolve(check)
        if check.phase == "after_query":
            if fault == "binding":
                value.authority = value.authority.model_copy(
                    update={"binding_revision": 2}
                )
            elif fault == "review":
                value.authority = value.authority.model_copy(
                    update={"review_id": uuid4()}
                )
            elif fault == "customer":
                value.customer_id = SecretStr("different-customer")
            elif fault == "session":
                value.principal = value.principal.model_copy(
                    update={"visitor_session_id": uuid4()}
                )
            else:
                value.snapshot = value.snapshot.model_copy(
                    update={
                        "connection": value.snapshot.connection.model_copy(
                            update={"enabled": False}
                        )
                    }
                )
        return value

    owner.resolve.side_effect = changed
    with pytest.raises(ConnectionAuthorityUnavailable):
        await runner.run(command)


@pytest.mark.asyncio
async def test_audit_unavailable_never_produces_factual_success(execution):
    command, _, _, audit, _, runner = execution
    audit.record.side_effect = RuntimeError("private-audit-marker")
    result = await runner.run(command)
    assert result.error_code == "audit_unavailable" and result.summary is None
    assert "private-audit-marker" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [None, "phase", "scope", "missing", "stale", "customer_context", "oversized"],
)
async def test_formal_owner_response_is_private_bounded_and_exact(execution, fault):
    from app.schemas.agent_business_execution import BusinessExecutionCheck

    command, resolve, _, _, _, _ = execution
    check = BusinessExecutionCheck(command=command, phase="before_query")
    value = resolve(check).model_dump(mode="json")
    if fault == "phase":
        value["authority"]["phase"] = "after_query"
    elif fault == "scope":
        value["scope"] = "employee_business_trial_resolution"
    elif fault == "missing":
        value["authority"].pop("can_query")
    elif fault == "stale":
        value["authority"]["checked_at"] = (
            datetime.now(UTC) - timedelta(minutes=3)
        ).isoformat()
    elif fault == "customer_context":
        value["principal"]["request_id"] = str(uuid4())
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, content=b"x" * 70000)
            if fault == "oversized"
            else httpx.Response(200, json=value)
        )
    )
    client = BusinessExecutionOwnerClient(transport=transport)
    if fault is None:
        assert (await client.resolve(check)).authority.matches(command)
    else:
        with pytest.raises(ConnectionAuthorityUnavailable) as error:
            await client.resolve(check)
        assert "private-context" not in str(error.value)
