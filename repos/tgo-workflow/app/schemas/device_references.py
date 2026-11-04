"""Device references exposed without workflow definitions or business inputs."""

from typing import Literal

from pydantic import BaseModel, Field


class DeviceWorkflowReference(BaseModel):
    workflow_id: str
    name: str
    source: Literal["draft", "published", "both"]


class DeviceWorkflowReferences(BaseModel):
    references: list[DeviceWorkflowReference] = Field(default_factory=list)
    total: int = Field(ge=0)
