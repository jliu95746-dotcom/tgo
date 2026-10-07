"""Operator-only merchant management contracts without credentials."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.schemas.operations_companies import OperationsCompany


class OperatorAuditView(BaseModel):
    id: UUID
    project_id: UUID | None
    operator_name: str
    action: str
    reason: str
    created_at: datetime
    detail: dict[str, JsonValue]


class ManagedMember(BaseModel):
    id: UUID
    username: str
    name: str | None
    role: str
    account_enabled: bool
    email_verified: bool
    token_version: int
    open_sessions: int


class CompanyDirectory(BaseModel):
    data: list[OperationsCompany]
    total: int
    offset: int
    limit: int


class CompanyDetail(BaseModel):
    company: OperationsCompany
    created_at: datetime
    plan_id: UUID | None
    version: int
    operator_override_until: datetime | None
    members: list[ManagedMember]
    audits: list[OperatorAuditView]


class AuthorizationSnapshot(BaseModel):
    plan_id: UUID | None
    plan_name: str | None
    status: str
    expires_at: datetime | None
    seats: int | None
    version: int
    ai_credits: int = 0


class AuthorizationChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    plan_id: UUID
    expires_at: datetime
    seats: int = Field(ge=1, le=10000, strict=True)
    ai_credits: int = Field(default=0, ge=0, le=100000000, strict=True)
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def require_timezone(self) -> "AuthorizationChange":
        if self.expires_at.tzinfo is None:
            raise ValueError("授权到期时间必须包含时区")
        return self


class AuthorizationPreview(BaseModel):
    before: AuthorizationSnapshot
    after: AuthorizationSnapshot
    used_seats: int
    reserved_seats: int


class MemberControl(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_token_version: int = Field(ge=1, strict=True)
    role: Literal["admin", "user"] | None = None
    account_enabled: bool | None = None
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> "MemberControl":
        if self.role is None and self.account_enabled is None:
            raise ValueError("请指定账号启用状态或角色")
        return self


class AccountEmailAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=5, max_length=500)


class OperationsOverview(BaseModel):
    total_companies: int
    enabled_companies: int
    expiring_companies: int
    unresolved_orders: int
    failed_tasks: int
    pending_invoices: int
    checked_at: datetime
