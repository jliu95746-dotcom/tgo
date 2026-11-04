"""Device scenario draft contract shared with device-control service."""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DeviceScenario(str, Enum):
    FILES = "files"
    REPORT = "report"


class DeviceScenarioDraftWrite(BaseModel):
    expected_revision: int = Field(ge=0)
    purpose: str = Field(min_length=1, max_length=255)
    business_system: str | None = Field(default=None, max_length=120)
    target_directory: str | None = Field(default=None, max_length=1024)

    @field_validator("business_system")
    @classmethod
    def normalize_business_system(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if any(ord(character) < 32 for character in stripped):
            raise ValueError("Business system contains control characters")
        return stripped or None

    @field_validator("purpose")
    @classmethod
    def nonempty_purpose(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Purpose cannot be blank")
        return stripped


class DeviceScenarioDraftRead(BaseModel):
    device_id: UUID
    project_id: UUID
    scenario: DeviceScenario
    purpose: str
    business_system: str | None = None
    target_directory: str | None
    revision: int
    status: Literal["draft"]
    updated_at: datetime
