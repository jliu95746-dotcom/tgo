"""Immutable producer identity shared with the existing AI execution check."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentExecutionReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    agent_id: UUID
    channel: Literal["wecom_kf", "web", "app", "phone", "internal"] | None
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    business_evidence_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
