"""One request's customer context; neither metadata nor the token activates an employee."""

from datetime import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.schemas.knowledge import KnowledgeChannel


def business_request_fingerprint(visitor: UUID, message: str) -> str:
    payload = {"visitor_id": str(visitor), "message": message, "message_type": 1}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


class BusinessCustomerSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: UUID
    customer_session: SecretStr = Field(min_length=1, max_length=8192, repr=False, exclude=True)


class BusinessContextBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    policy_revision: int = Field(ge=1, strict=True)
    connection_configuration_revision: int = Field(ge=1, strict=True)
    expires_at: datetime


class BusinessContextIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    visitor_id: UUID
    channel: KnowledgeChannel
    request_id: UUID
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class BusinessContextCheck(BusinessContextIdentity):
    context_token: SecretStr = Field(min_length=1, max_length=16384, repr=False, exclude=True)


class BusinessContextClaims(BusinessContextIdentity):
    aud: Literal["tgo-business-execution-context"] = "tgo-business-execution-context"
    type: Literal["business_request_context"] = "business_request_context"
    platform_id: UUID
    visitor_session_id: UUID
    source_message_id: str = Field(min_length=1, max_length=255)
    response_client_msg_no: str = Field(min_length=1, max_length=100)
    bindings: list[BusinessContextBinding] = Field(min_length=1, max_length=20)
    iat: int = Field(ge=0, strict=True)
    exp: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def bounded_scope(self) -> "BusinessContextClaims":
        if (not 0 < self.exp - self.iat <= 300
            or len({row.connection_id for row in self.bindings}) != len(self.bindings)
            or any(row.expires_at.tzinfo is None or row.expires_at.timestamp() < self.exp for row in self.bindings)):
            raise ValueError("业务请求上下文范围或有效期不完整")
        return self


class BusinessRequestAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    visitor_id: UUID
    channel: KnowledgeChannel
    request_id: UUID
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    context_token: SecretStr = Field(repr=False, exclude=True)


class BusinessRequestPrincipal(BusinessContextIdentity):
    scope: Literal["business_request_principal"] = "business_request_principal"
    can_enable: Literal[False] = False
    platform_id: UUID
    visitor_session_id: UUID
    source_message_id: str = Field(min_length=1, max_length=255)
    response_client_msg_no: str = Field(min_length=1, max_length=100)
    bindings: list[BusinessContextBinding] = Field(min_length=1, max_length=20)
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def current_context(self) -> "BusinessRequestPrincipal":
        if (self.checked_at.tzinfo is None or self.expires_at.tzinfo is None
            or self.expires_at <= self.checked_at
            or len({row.connection_id for row in self.bindings}) != len(self.bindings)
            or any(row.expires_at.tzinfo is None or row.expires_at < self.expires_at for row in self.bindings)):
            raise ValueError("业务请求上下文已失效或重复")
        return self
