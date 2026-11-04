"""Issuer and customer ciphertext use separate authenticated namespaces."""

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from jose import jwt
from pydantic import JsonValue, SecretStr, ValidationError

from app.core.exceptions import TGOAPIException
from app.models.business_customer_identity import BusinessCustomerBinding
from app.schemas.business_customer_identity import (
    MerchantCustomerAssertion,
    CustomerIdentitySession,
    CustomerSessionClaims,
)
from app.services.business_connection_vault import CredentialVault


class CustomerIssuerVault(CredentialVault):
    @staticmethod
    def _scope(project: UUID, connection: UUID) -> bytes:
        return f"tgo-business-customer-issuer-v1:{project}:{connection}".encode()


class CustomerBindingVault(CredentialVault):
    @staticmethod
    def _scope(project: UUID, connection: UUID) -> bytes:
        return f"tgo-business-customer-binding-v1:{project}:{connection}".encode()


def identity_denied() -> TGOAPIException:
    return TGOAPIException(
        "客户身份无法核验或已失效，请重新验证或转人工",
        code="CUSTOMER_IDENTITY_UNVERIFIED",
        status_code=403,
    )


def unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate assertion field")
        result[key] = value
    return result


def parse_assertion(value: SecretStr) -> tuple[MerchantCustomerAssertion, str, str]:
    try:
        version, payload, signature = value.get_secret_value().split(".")
        if (
            version != "v1"
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,16000}", payload)
            or not re.fullmatch(r"[0-9a-f]{64}", signature)
        ):
            raise ValueError
        raw = base64.b64decode(
            payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
        )
        decoded = json.loads(raw, object_pairs_hook=unique_object)
        claims = MerchantCustomerAssertion.model_validate(decoded)
        return claims, payload, signature
    except (
        ValueError,
        binascii.Error,
        UnicodeDecodeError,
        ValidationError,
        RecursionError,
    ):
        raise identity_denied() from None


def verify_signature(key: SecretStr, payload: str, signature: str) -> None:
    expected = hmac.new(
        key.get_secret_value().encode(),
        b"tgo-customer-identity-v1:" + payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise identity_denied()


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def session_signing_key() -> bytes:
    return hmac.new(
        CredentialVault()._key(),
        b"tgo-business-customer-session-signing-v1",
        hashlib.sha256,
    ).digest()


def issue_session(row: BusinessCustomerBinding) -> CustomerIdentitySession:
    claims = CustomerSessionClaims(
        aud="tgo-business-customer-session",
        type="business_customer_session",
        sub=row.id,
        project_id=row.project_id,
        connection_id=row.connection_id,
        visitor_id=row.visitor_id,
        platform_id=row.platform_id,
        revision=row.revision,
        policy_revision=row.policy_revision,
        iat=int(utc(row.issued_at).timestamp()),
        exp=int(utc(row.expires_at).timestamp()),
    )
    token = jwt.encode(
        claims.model_dump(mode="json"), session_signing_key(), algorithm="HS256"
    )
    return CustomerIdentitySession(
        binding_id=row.id,
        project_id=row.project_id,
        connection_id=row.connection_id,
        visitor_id=row.visitor_id,
        platform_id=row.platform_id,
        revision=row.revision,
        expires_at=utc(row.expires_at),
        session_token=SecretStr(token),
    )
