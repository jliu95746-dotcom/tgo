"""Current authority check for the workflow runner on the private listener."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.internal.endpoints.workflow_approvals import (
    require_workflow_service,
)
from app.core.database import get_db
from app.schemas.workflow_event_subscriptions import WorkflowEventSourceCheck
from app.services.workflow_event_subscriptions import verify_source_binding

router = APIRouter(dependencies=[Depends(require_workflow_service)])


@router.post("/source/verify", response_model=WorkflowEventSourceCheck)
def verify_event_source(
    query: WorkflowEventSourceCheck,
    response: Response,
    db: Session = Depends(get_db),
) -> WorkflowEventSourceCheck:
    response.headers["Cache-Control"] = "no-store"
    return verify_source_binding(db, query)
