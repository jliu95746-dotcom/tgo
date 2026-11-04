"""The admin SDK follows the owner contract without exposing private snapshots."""

from pathlib import Path
import re
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.business_connections import (
    BusinessConnectionCreate,
    BusinessConnectionResponse,
    BusinessHTTPConfiguration,
)
from app.schemas.business_connection_tests import (
    BusinessConnectionTestRequest,
    BusinessConnectionTestReport,
    BusinessConnectionTestApplicability,
)
from app.schemas.connection_test_protocol import ConnectionTestSummary


def fields(interface: str) -> set[str]:
    source = Path(__file__).parents[2] / "tgo-web/src/types/businessConnections.ts"
    content = source.read_text(encoding="utf-8")
    match = re.search(r"export interface " + interface + r"\s*\{([^}]+)\}", content)
    assert match is not None
    return set(re.findall(r"^\s*(\w+)\??:", match.group(1), re.MULTILINE))


def test_admin_sdk_response_and_http_mapping_fields_match_the_owner():
    assert fields("BusinessConnection") == set(BusinessConnectionResponse.model_fields)
    assert fields("BusinessHTTPConfiguration") == set(
        BusinessHTTPConfiguration.model_fields
    )


def test_public_response_contract_cannot_export_credentials_or_private_resolution():
    properties = BusinessConnectionResponse.model_json_schema()["properties"]
    assert "credentials" not in properties and "credential_ciphertext" not in properties
    assert "request_digest" not in properties
    sdk = (
        Path(__file__).parents[2] / "tgo-web/src/services/businessConnectionsApi.ts"
    ).read_text(encoding="utf-8")
    assert "/internal/" not in sdk and "SERVICE_TOKEN" not in sdk


def test_public_test_request_report_and_summary_match_web_sdk():
    assert fields("BusinessConnectionTestRequest") == set(
        BusinessConnectionTestRequest.model_fields
    )
    assert fields("BusinessConnectionTestReport") == set(
        BusinessConnectionTestReport.model_fields
    )
    assert fields("BusinessConnectionTestSummary") == set(
        ConnectionTestSummary.model_fields
    )


def test_private_configuration_and_test_protocol_copies_match():
    api = Path(__file__).parents[1] / "app/schemas"
    plugin = Path(__file__).parents[2] / "tgo-plugin-runtime/app/schemas"
    for name in ("business_connections.py", "connection_test_protocol.py"):
        assert (api / name).read_bytes() == (plugin / name).read_bytes()


def test_public_applicability_fields_match_web_sdk():
    assert fields("BusinessConnectionTestApplicability") == set(
        BusinessConnectionTestApplicability.model_fields
    )


def test_customer_read_mapping_is_saved_as_project_connection_configuration():
    request = BusinessConnectionCreate(
        request_id=uuid4(),
        name="客户资料查询连接",
        capabilities=["order_query"],
        configuration=BusinessHTTPConfiguration(
            base_url="https://merchant.example.test",
            customer_profile_path="/v1/customers/profile/query",
            membership_path="/v1/memberships/query",
            customer_segment_field="profile.segment",
            membership_tier_field="member.tier",
        ),
    )
    stored = request.configuration.model_dump(mode="json")
    assert stored["customer_profile_path"] == "/v1/customers/profile/query"
    assert stored["membership_tier_field"] == "member.tier"
    assert request.enabled is False

    with pytest.raises(ValidationError):
        BusinessHTTPConfiguration(
            base_url="https://merchant.example.test",
            customer_profile_path="/v1/customers/profile/query",
        )


def test_customer_configuration_test_requires_customer_but_not_order_number():
    request = BusinessConnectionTestRequest(
        request_id=uuid4(), expected_revision=1,
        operation="membership_query", customer_id="customer-test",
    )
    assert request.order_no is None
    with pytest.raises(ValidationError):
        BusinessConnectionTestRequest(
            request_id=uuid4(), expected_revision=1,
            operation="order_query", customer_id="customer-test",
        )
    with pytest.raises(ValidationError):
        BusinessConnectionTestRequest(
            request_id=uuid4(), expected_revision=1,
            operation="customer_profile_query", customer_id="customer-test",
            order_no="ORDER-001",
        )


def test_customer_capability_requires_real_post_paths():
    fields = {
        "request_id": uuid4(),
        "name": "会员连接",
        "capabilities": ["membership_query"],
    }
    with pytest.raises(ValidationError):
        BusinessConnectionCreate(**fields, mode="demo")
    with pytest.raises(ValidationError):
        BusinessConnectionCreate(
            **fields, configuration={"base_url": "https://merchant.example.test"}
        )
    with pytest.raises(ValidationError):
        BusinessConnectionCreate(
            **fields,
            configuration={
                "base_url": "https://merchant.example.test",
                "customer_profile_path": "/v1/customers/profile/query",
                "membership_path": "/v1/memberships/query",
                "method": "GET",
            },
        )
