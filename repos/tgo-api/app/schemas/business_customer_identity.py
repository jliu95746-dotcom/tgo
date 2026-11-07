"""Identity confirmation is distinct from a grant to execute business tools."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)


class IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerIdentityPolicyWrite(IdentityModel):
    expected_revision: int = Field(ge=0, strict=True)
    platform_ids: list[UUID] = Field(min_length=1, max_length=50)
    signing_secret: SecretStr | None = Field(
        default=None, min_length=1, max_length=512
    )
    enabled: bool = False
    max_session_seconds: int = Field(default=900, ge=60, le=3600, strict=True)

    @field_validator("platform_ids")
    @classmethod
    def unique_platforms(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("渠道不能重复")
        return value

    @field_validator("signing_secret")
    @classmethod
    def signing_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and (
            len(value.get_secret_value().encode()) < 32
            or any(
                ord(char) < 32 or ord(char) == 127
                for char in value.get_secret_value()
            )
        ):
            raise ValueError("签名密钥至少32字节，不能包含控制字符")
        return value


class CustomerIdentityPolicyView(IdentityModel):
    scope: Literal[
        "business_customer_identity_configuration"
    ] = "business_customer_identity_configuration"
    can_enable: Literal[False] = False
    id: UUID
    project_id: UUID
    connection_id: UUID
    platform_ids: list[UUID]
    enabled: bool
    revision: int
    connection_configuration_revision: int
    max_session_seconds: int
    signing_key_configured: bool


class MerchantCustomerAssertion(IdentityModel):
    purpose: Literal["business_customer_identity"]
    project_id: UUID
    connection_id: UUID
    platform_id: UUID
    visitor_id: UUID
    customer_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
    )
    nonce: UUID
    policy_revision: int = Field(ge=1, strict=True)
    issued_at: int = Field(ge=0, strict=True)
    expires_at: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def expires_after_issue(self) -> "MerchantCustomerAssertion":
        if self.expires_at <= self.issued_at:
            raise ValueError("身份验证有效期必须晚于签发时间")
        return self


class CustomerIdentityExchangeRequest(IdentityModel):
    merchant_assertion: SecretStr = Field(min_length=1, max_length=16384)
    visitor_access_token: SecretStr | None = Field(
        default=None, min_length=1, max_length=256, repr=False, exclude=True
    )


class CustomerIdentitySession(IdentityModel):
    scope: Literal["business_customer_identity"] = "business_customer_identity"
    can_enable: Literal[False] = False
    binding_id: UUID
    project_id: UUID
    connection_id: UUID
    visitor_id: UUID
    platform_id: UUID
    revision: int
    expires_at: datetime
    session_token: SecretStr

    @field_serializer("session_token")
    def issued_token(self, value: SecretStr) -> str:
        return value.get_secret_value()


class CustomerIdentityRevisionRequest(IdentityModel):
    expected_revision: int = Field(ge=1, strict=True)


class CustomerIdentityBindingView(IdentityModel):
    id: UUID
    visitor_id: UUID
    platform_id: UUID
    revision: int
    expires_at: datetime
    revoked_at: datetime | None
    status: Literal["current", "expired", "revoked", "stale"]
    can_enable: Literal[False] = False


class VerifiedBusinessCustomer(IdentityModel):
    scope: Literal[
        "business_customer_identity_check"
    ] = "business_customer_identity_check"
    can_enable: Literal[False] = False
    project_id: UUID
    connection_id: UUID
    visitor_id: UUID
    platform_id: UUID
    binding_id: UUID
    revision: int
    policy_revision: int
    connection_configuration_revision: int
    expires_at: datetime
    customer_id: SecretStr


class CustomerSessionClaims(IdentityModel):
    aud: Literal["tgo-business-customer-session"]
    type: Literal["business_customer_session"]
    sub: UUID
    project_id: UUID
    connection_id: UUID
    visitor_id: UUID
    platform_id: UUID
    revision: int = Field(ge=1, strict=True)
    policy_revision: int = Field(ge=1, strict=True)
    iat: int = Field(ge=0, strict=True)
    exp: int = Field(ge=0, strict=True)
