"""Management selects reviewed metadata without submitting code."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.device_script_policy import ScriptPolicy, ScriptSessionStatus


class ScriptPolicyPublish(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_policy_revision: int = Field(ge=0, lt=2147483647)
    connection_instance_id: str = Field(
        min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$"
    )
    session_id: str = Field(
        min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$"
    )
    script_id: str = Field(
        min_length=1, max_length=48, pattern=r"^[A-Za-z][A-Za-z0-9_]*$"
    )
    grant_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    timeout_seconds: int = Field(ge=1, le=120)
    max_output_bytes: int = Field(ge=256, le=65536)
    max_runs: int = Field(ge=1, le=100)


class ScriptPolicyRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_policy_revision: int = Field(ge=1, lt=2147483647)


class ScriptLocalStatusRead(BaseModel):
    device_id: UUID
    project_id: UUID
    connection_instance_id: str | None = None
    session: ScriptSessionStatus


class ScriptPolicyRead(BaseModel):
    device_id: UUID
    project_id: UUID
    policy: ScriptPolicy
    status: Literal["pending", "active", "revoked"]
    verified: Literal[False] = False
    updated_at: datetime
    revoked_at: datetime | None = None
