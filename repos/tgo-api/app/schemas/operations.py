"""Explicit public contracts for the 域见 platform operations console."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.schemas.base import BaseSchema, PaginationMetadata


class OperatorLoginRequest(BaseSchema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def bound_password_bytes(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
        return value


class OperatorCreateRequest(OperatorLoginRequest):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入运营人员名称")
        return value

    @field_validator("password")
    @classmethod
    def require_strong_password(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 12:
            raise ValueError("运营账号密码至少 12 位")
        return value


class OperatorResponse(BaseSchema):
    id: UUID
    email: str
    name: str


class OperatorLoginResponse(BaseSchema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    operator: OperatorResponse


class OperationsStatus(BaseSchema):
    enabled: bool
    login_available: bool


class CompanyMigrationPreview(BaseSchema):
    project_id: UUID
    name: str
    created_at: datetime
    human_accounts: int
    administrator_accounts: int
    action: Literal["review_required"] = "review_required"
    requires_admin_recovery: bool


class MigrationPreviewResponse(BaseSchema):
    will_change_data: Literal[False] = False
    data: list[CompanyMigrationPreview]
    pagination: PaginationMetadata
