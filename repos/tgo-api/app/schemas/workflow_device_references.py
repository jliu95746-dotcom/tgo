"""Tenant-scoped summaries for device workflow references."""

from typing import Literal

from pydantic import Field

from app.schemas.base import BaseSchema


class WorkflowDeviceReference(BaseSchema):
    workflow_id: str
    name: str
    source: Literal["draft", "published", "both"]


class WorkflowDeviceReferences(BaseSchema):
    references: list[WorkflowDeviceReference] = Field(default_factory=list)
    total: int = Field(ge=0)
