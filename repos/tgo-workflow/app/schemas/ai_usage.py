"""Private coordinates shared with API billing; no bearer secrets are persisted."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_runtime import AgentRuntimeRequest


class WorkflowUsageOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    staff_id: UUID | None = None
    staff_token_version: int | None = Field(default=None, ge=1)
    reservation_id: UUID | None = None
    lease_id: UUID | None = None
    unmetered: Literal[True] | None = None

    @model_validator(mode="after")
    def require_one_complete_owner(self) -> "WorkflowUsageOwner":
        staff = (
            self.staff_id is not None and self.staff_token_version is not None
        )
        inherited = (
            self.reservation_id is not None and self.lease_id is not None
        )
        if sum((staff, inherited, self.unmetered is True)) != 1 or (
            (self.staff_id is None) != (self.staff_token_version is None)
            or (self.reservation_id is None) != (self.lease_id is None)
        ):
            raise ValueError("Workflow requires one complete billing owner")
        return self


class WorkflowAIContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    execution_id: UUID
    node_id: str = Field(min_length=1, max_length=128)
    agent_id: UUID
    owner: WorkflowUsageOwner
    status: Literal["running"]
    workflow_lineage: tuple[str, ...] = Field(default=(), max_length=4)
    expected_device_id: UUID | None = None
    expected_device_policy_digest: str | None = None


class WorkflowAICommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    execution_id: UUID
    node_id: str = Field(min_length=1, max_length=128)
    payload: AgentRuntimeRequest
