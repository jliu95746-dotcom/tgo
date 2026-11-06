"""Private, current-state membership check for workflow approval nodes."""

from secrets import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.workflow_approval import (
    WorkflowApprovalAssigneeCheck,
    WorkflowApprovalAssigneeReceipt,
)
from app.services.workflow_approval_assignee import require_approval_staff_ids


def require_workflow_service(
    x_internal_api_key: str | None = Header(None),
) -> None:
    if (
        not settings.SECRET_KEY
        or not x_internal_api_key
        or not compare_digest(
            x_internal_api_key.encode(),
            settings.SECRET_KEY.encode(),
        )
    ):
        raise HTTPException(403, "内部服务身份无效")


router = APIRouter(dependencies=[Depends(require_workflow_service)])


@router.post(
    "/assignees/verify", response_model=WorkflowApprovalAssigneeReceipt
)
def verify_approval_assignees(
    query: WorkflowApprovalAssigneeCheck,
    db: Session = Depends(get_db),
) -> WorkflowApprovalAssigneeReceipt:
    assignee_ids = set(query.assignee_ids)
    if len(assignee_ids) != len(query.assignee_ids):
        raise HTTPException(422, "审批人列表包含重复编号")
    require_approval_staff_ids(
        assignee_ids, db, query.project_id, status_code=409
    )
    return WorkflowApprovalAssigneeReceipt(
        project_id=query.project_id,
        assignee_ids=sorted(assignee_ids),
    )
