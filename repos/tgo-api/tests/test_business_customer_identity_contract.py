"""Admin metadata is synchronized; business bearer credentials remain separate."""

from pathlib import Path
import re

from app.schemas.business_customer_identity import (
    CustomerIdentityPolicyView,
    CustomerIdentityPolicyWrite,
    CustomerIdentityBindingView,
)


def fields(name):
    source = (
        Path(__file__).parents[2] / "tgo-web/src/types/businessCustomerIdentity.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"export interface " + name + r"\s*\{([^}]+)\}", source)
    assert match is not None
    return set(re.findall(r"(\w+)\??\s*:", match.group(1)))


def test_customer_policy_binding_and_write_sdk_match_owner_fields():
    for frontend, backend in [
        ("CustomerIdentityPolicy", CustomerIdentityPolicyView),
        ("CustomerIdentityPolicyWrite", CustomerIdentityPolicyWrite),
        ("CustomerIdentityBinding", CustomerIdentityBindingView),
    ]:
        assert fields(frontend) == set(backend.model_fields)


def test_management_sdk_cannot_exchange_customer_assertions_or_export_credentials():
    root = Path(__file__).parents[2] / "tgo-web/src"
    sdk = (root / "services/businessCustomerIdentityApi.ts").read_text(encoding="utf-8")
    assert "/exchange" not in sdk and "/internal/" not in sdk
    response = set(CustomerIdentityPolicyView.model_fields) | set(
        CustomerIdentityBindingView.model_fields
    )
    assert not response & {
        "customer_id",
        "signing_secret",
        "signing_ciphertext",
        "session_token",
        "customer_ciphertext",
    }
