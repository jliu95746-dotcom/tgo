"""Read-only device execution policy contract."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeviceReadPolicyPublish(BaseModel):
    expected_draft_revision: int = Field(ge=1)
    expected_policy_revision: int = Field(ge=0)
    max_lines: int = Field(default=100, ge=1, le=500)
    max_offset: int = Field(default=10000, ge=0, le=100000)


class DeviceReadPolicyTest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    expected_text: str = Field(min_length=4, max_length=100)
    limit: int = Field(default=20, ge=1, le=500)

    @field_validator("expected_text")
    @classmethod
    def require_visible_marker(cls, value: str) -> str:
        marker = value.strip()
        if len(marker) < 4:
            raise ValueError(
                "Read test marker must have at least four visible characters"
            )
        return marker


class DeviceReadPolicyRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_policy_revision: int = Field(strict=True, ge=1, lt=2147483647)


class DeviceReadPolicyRead(BaseModel):
    device_id: UUID
    project_id: UUID
    revision: int
    tool_name: Literal["fs_read"] = "fs_read"
    allowed_directory: str
    max_lines: int
    max_offset: int
    digest: str
    status: Literal["active", "pending", "revoked"]
    verified: bool
    last_tested_at: datetime | None = None
    last_test_status: Literal["passed", "failed"] | None = None
    updated_at: datetime
    revoked_at: datetime | None = None
