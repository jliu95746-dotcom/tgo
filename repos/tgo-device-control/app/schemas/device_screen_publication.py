"""Management contracts exclude window identity and any client-supplied test pass."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.schemas.device_screen_policy import (
    ScreenGrantStatus,
    ScreenPolicy,
    ScreenRegion,
)


class ScreenPolicyPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_policy_revision: int = Field(strict=True, ge=0, lt=2147483647)
    connection_instance_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    grant_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    max_actions: int = Field(strict=True, ge=1, le=50)
    allow_click: StrictBool
    allow_text: StrictBool
    click_regions: list[ScreenRegion] = Field(max_length=16)

    @model_validator(mode="after")
    def finite_click_scope(self) -> "ScreenPolicyPublish":
        if self.allow_click != bool(self.click_regions):
            raise ValueError("Click permission and finite regions must agree")
        return self


class ScreenPolicyRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_policy_revision: int = Field(strict=True, ge=1, lt=2147483647)


class ScreenLocalGrantRead(BaseModel):
    device_id: UUID
    project_id: UUID
    connection_instance_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    grant: ScreenGrantStatus


class ScreenPolicyRead(BaseModel):
    device_id: UUID
    project_id: UUID
    policy: ScreenPolicy
    status: Literal["pending", "active", "revoked"]
    verified: Literal[False] = False
    updated_at: datetime
    revoked_at: datetime | None = None
