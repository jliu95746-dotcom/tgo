"""Retain the original staff token without putting it in outputs or task JSON."""

from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class StaffDataAccess(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    staff_id: UUID
    project_id: UUID
    username: str
    role: str
    token_version: int
    token: SecretStr = Field(exclude=True, repr=False)
