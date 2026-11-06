"""Check approval assignees against the current enterprise membership."""

from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.staff import Staff
from app.schemas.ai_workflows import (
    ApprovalNodeData,
    WorkflowNodeInput,
    WorkflowNodeOutput,
)


def require_approval_assignees(
    nodes: Sequence[WorkflowNodeInput | WorkflowNodeOutput],
    db: Session,
    project_id: UUID,
) -> None:
    """A human approver must be an enabled user or admin of this project."""
    assignee_ids = {
        UUID(node.data.approver_staff_id)
        for node in nodes
        if isinstance(node.data, ApprovalNodeData)
    }
    require_approval_staff_ids(assignee_ids, db, project_id, status_code=422)


def require_approval_staff_ids(
    assignee_ids: set[UUID],
    db: Session,
    project_id: UUID,
    *,
    status_code: int,
) -> None:
    """Check the current account state without exposing staff information."""
    if not assignee_ids:
        return
    available = set(
        db.scalars(
            select(Staff.id).where(
                Staff.project_id == project_id,
                Staff.id.in_(assignee_ids),
                Staff.account_enabled.is_(True),
                Staff.deleted_at.is_(None),
                Staff.role.in_(("user", "admin")),
            )
        ).all()
    )
    if available != assignee_ids:
        raise HTTPException(
            status_code,
            "审批人必须是本企业可登录的人工员工，请重新选择。",
        )
