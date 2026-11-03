"""Formal queries need an exact run/customer/binding scope, not a test report token."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionAuthority,
)


def command():
    project, agent, visitor, request = [uuid4() for _ in range(4)]
    return BusinessExecutionCommand(
        query_id=uuid4(),
        connection_id=uuid4(),
        binding_id=uuid4(),
        binding_revision=1,
        operation="order_query",
        context_digest="d" * 64,
        order_no=SecretStr("private-order"),
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


def test_private_wire_contains_required_secrets_but_safe_serialization_does_not():
    value = command()
    assert value.private_payload()["order_no"] == "private-order"
    assert value.private_payload()["context"]["context_token"] == "private-context"
    assert "private-order" not in value.model_dump_json()
    assert "private-context" not in value.model_dump_json()
    assert "private-order" not in repr(value)


@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
def test_customer_formal_query_uses_original_context_without_order_number(operation):
    payload = command().private_payload()
    payload["operation"] = operation
    del payload["order_no"]
    value = BusinessExecutionCommand.model_validate(payload)
    assert value.order_no is None
    assert "order_no" not in value.private_payload()
    with pytest.raises(ValidationError):
        BusinessExecutionCommand.model_validate({**payload, "order_no": "PRIVATE-ORDER-1"})


def test_order_formal_query_still_requires_order_number():
    payload = command().private_payload()
    del payload["order_no"]
    with pytest.raises(ValidationError):
        BusinessExecutionCommand.model_validate(payload)


@pytest.mark.parametrize(
    "fault", ["project", "channel", "reference", "purpose", "operation"]
)
def test_foreign_or_test_purpose_cannot_become_formal_query(fault):
    payload = command().private_payload()
    if fault == "project":
        payload["context"]["project_id"] = str(uuid4())
    elif fault == "channel":
        payload["context"]["channel"] = "wecom_kf"
    elif fault == "reference":
        payload["reference"]["business_evidence_revision"] = None
    elif fault == "purpose":
        payload["scope"] = "employee_business_trial"
    else:
        payload["operation"] = "refund"
    with pytest.raises(ValidationError):
        BusinessExecutionCommand.model_validate(payload)


def test_authority_is_bound_to_exact_command_and_future_expiry():
    value = command()
    body = {
        **value.identity(),
        "actor_id": uuid4(),
        "phase": "before_query",
        "connection_revision": 1,
        "configuration_revision": 1,
        "authorization_revision": 1,
        "resource_revision": "a" * 64,
        "report_id": uuid4(),
        "review_id": uuid4(),
        "configuration_test_id": uuid4(),
        "checked_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(minutes=1),
    }
    authority = BusinessExecutionAuthority(**body)
    assert (
        authority.matches(value)
        and authority.can_query
        and authority.can_enable is False
    )
    assert not authority.matches(value.model_copy(update={"query_id": uuid4()}))
    with pytest.raises(ValidationError):
        BusinessExecutionAuthority(
            **{**body, "expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
