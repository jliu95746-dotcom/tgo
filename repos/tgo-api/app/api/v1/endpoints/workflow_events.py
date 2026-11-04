"""Administrator-only testing of a workflow's fixed event publication."""

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.workflow_events import (
    WorkflowEventAccepted,
    WorkflowEventTestContext,
    WorkflowEventTestRequest,
)
from app.services.workflow_client import workflow_client
from app.schemas.workflow_event_subscriptions import (
    WorkflowEventSubscriptionUpdate,
    WorkflowEventSubscriptionSummary,
    WorkflowEventDeliveryList,
)
from app.services import workflow_event_subscriptions as subscriptions

router = APIRouter()


@router.get(
    "/{workflow_id}/event-subscription",
    response_model=WorkflowEventSubscriptionSummary,
)
def get_event_subscription(
    response: Response,
    workflow_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WorkflowEventSubscriptionSummary:
    response.headers["Cache-Control"] = "no-store"
    return subscriptions.get_subscription(
        db, current_user.project_id, workflow_id
    )


@router.put(
    "/{workflow_id}/event-subscription",
    response_model=WorkflowEventSubscriptionSummary,
)
async def set_event_subscription(
    request: WorkflowEventSubscriptionUpdate,
    response: Response,
    workflow_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WorkflowEventSubscriptionSummary:
    response.headers["Cache-Control"] = "no-store"
    return await subscriptions.update_subscription(
        db, current_user, workflow_id, request
    )


@router.delete(
    "/{workflow_id}/event-subscription",
    response_model=WorkflowEventSubscriptionSummary,
)
async def disable_event_subscription(
    response: Response,
    expected_revision: int = Query(ge=0),
    workflow_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WorkflowEventSubscriptionSummary:
    response.headers["Cache-Control"] = "no-store"
    return await subscriptions.update_subscription(
        db,
        current_user,
        workflow_id,
        WorkflowEventSubscriptionUpdate(
            enabled=False, expected_revision=expected_revision
        ),
    )


@router.get(
    "/{workflow_id}/event-deliveries", response_model=WorkflowEventDeliveryList
)
def list_event_deliveries(
    response: Response,
    workflow_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> WorkflowEventDeliveryList:
    response.headers["Cache-Control"] = "no-store"
    return subscriptions.list_deliveries(
        db, current_user.project_id, workflow_id
    )


@router.get(
    "/{workflow_id}/event-test-context",
    response_model=WorkflowEventTestContext,
)
async def get_event_test_context(
    response: Response,
    workflow_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    current_user: Staff = Depends(require_admin()),
) -> WorkflowEventTestContext:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_client.get_event_test_context(
        workflow_id,
        str(current_user.project_id),
    )


@router.post(
    "/{workflow_id}/event-tests",
    response_model=WorkflowEventAccepted,
    status_code=202,
)
async def send_event_test(
    request: WorkflowEventTestRequest,
    response: Response,
    workflow_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    current_user: Staff = Depends(require_admin()),
) -> WorkflowEventAccepted:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_client.send_event_test(
        workflow_id,
        str(current_user.project_id),
        str(current_user.id),
        request,
    )
