"""Read-only impact report for employee workflow bindings without a release pin."""

from uuid import UUID

from app.schemas.base import BaseSchema, PaginatedResponse


class WorkflowReleaseAuditItem(BaseSchema):
    binding_id: UUID
    agent_id: UUID
    agent_name: str
    agent_active: bool
    agent_default: bool
    workflow_id: str
    workflow_enabled: bool
    workflow_name: str | None
    workflow_status: str | None
    current_published_version: int | None
    details_available: bool


class WorkflowReleaseAuditResponse(PaginatedResponse[WorkflowReleaseAuditItem]):
    """One stable page of locally stored bindings requiring review."""
