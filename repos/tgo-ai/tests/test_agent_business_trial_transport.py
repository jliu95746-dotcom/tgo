"""No credential/global-provider fallback; only complete exact-purpose receipts."""

from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.exceptions import ExternalServiceError
from app.schemas.agent_business_trials import (
    BusinessTrialCommand,
    BusinessTrialReceipt,
    BusinessTrialCustomerEvidence,
)
from app.services.business_trial_query_client import query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "report",
        "operation",
        "scope",
        "missing",
        "future",
        "customer",
        "large",
        "error",
    ],
)
async def test_exact_complete_fresh_business_receipt(change):
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
        customer_session=SecretStr("private-customer-session"),
    )
    result = BusinessTrialReceipt(
        **command.identity(),
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
    if change == "operation":
        body["operation"] = "logistics_query"
    if change == "scope":
        body["scope"] = "business_connection_test"
    if change == "missing":
        del body["can_enable"]
    if change == "future":
        body["checked_at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    if change == "customer":
        body["customer_evidence"]["expires_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()

    def respond(request):
        assert (
            request.url.path == "/internal/ai/business-trials/query"
            and json.loads(request.content) == command.private_payload()
        )
        assert request.headers.get("X-Internal-API-Key")
        assert "credentials" not in json.loads(request.content)
        if change == "large":
            return httpx.Response(200, content="x" * 65537)
        if change == "error":
            return httpx.Response(500, text="private-upstream-secret")
        return httpx.Response(200, json=body)

    if change == "valid":
        saved = await query(
            "http://owner/internal", command, transport=httpx.MockTransport(respond)
        )
        assert saved.report_id == command.report_id and not saved.can_enable
    else:
        with pytest.raises(ExternalServiceError) as error:
            await query(
                "http://owner/internal", command, transport=httpx.MockTransport(respond)
            )
        assert "private-upstream-secret" not in str(error.value)
