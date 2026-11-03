"""Executable workflow capabilities, distinct from registered placeholder nodes."""

from typing import Literal

from pydantic import BaseModel, Field

ExecutionMode = Literal["draft_test", "production"]


class WorkflowNodeCapability(BaseModel):
    type: str
    available: bool
    preparation_available: bool = False
    required_fields: list[str] = Field(default_factory=list)
    reason: str | None = None


class WorkflowCapabilities(BaseModel):
    nodes: list[WorkflowNodeCapability]
    execution_modes: list[ExecutionMode]
