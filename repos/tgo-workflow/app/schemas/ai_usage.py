"""Private API billing coordinates; bearer secrets are never persisted."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.schemas.agent_runtime import AgentRuntimeRequest
from app.schemas.agent_execution import AgentExecutionReference


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


class WorkflowAIResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    success: StrictBool
    content: str = Field(min_length=1, max_length=100000)
    execution_id: UUID
    execution_reference: AgentExecutionReference | None = None

    @model_validator(mode="after")
    def require_completed_answer(self) -> "WorkflowAIResult":
        if self.success is not True or not self.content.strip():
            raise ValueError("A saved AI result must be a successful answer")
        return self


class WorkflowAICacheCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command: WorkflowAICommand
    reservation_id: UUID | None = None
    lease_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_permit(self) -> "WorkflowAICacheCommand":
        if (self.reservation_id is None) != (self.lease_id is None):
            raise ValueError("A saved result requires a complete quota permit")
        return self


class WorkflowAICacheWrite(WorkflowAICacheCommand):
    claim_id: UUID
    result: WorkflowAIResult

    @model_validator(mode="after")
    def require_original_producer(self) -> "WorkflowAICacheWrite":
        ref = self.result.execution_reference
        if (
            ref is None
            or ref.project_id != self.command.project_id
            or ref.agent_id != self.command.payload.agent_id
            or ref.channel != "internal"
        ):
            raise ValueError("Saved answers require their exact producer")
        return self


class WorkflowAICacheReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["claimed", "pending", "completed"]
    claim_id: UUID
    result: WorkflowAIResult | None = None

    @model_validator(mode="after")
    def require_completed_result(self) -> "WorkflowAICacheReceipt":
        if (self.state == "completed") != (self.result is not None):
            raise ValueError("Only completed calls can return a saved answer")
        return self
