"""Private AI service contract for accepting an employee workflow run."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EmployeeWorkflowBindingReference(BaseModel):
    project_id: UUID
    agent_id: UUID
    workflow_id: str = Field(min_length=1, max_length=36)
    release_version: int = Field(ge=1, strict=True)
    release_id: str = Field(min_length=1, max_length=36)
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class EmployeeWorkflowBindingReceipt(BaseModel):
    authorized: Literal[True]
