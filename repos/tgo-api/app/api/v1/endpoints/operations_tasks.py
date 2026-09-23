"""Independent operator authorization for diagnostics and retry requests."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.operations import (
    require_operator,
    require_commercial_operations,
)
from app.core.database import get_db
from app.models.billing import BillingAudit, BillingJob
from app.models.platform_operator import PlatformOperator
from app.schemas.operations_tasks import AuditResponse, RetryTask, TaskResponse
from app.services.operations_tasks import retry_task
from app.schemas.model_usage import ModelUsageResponse
from app.services.model_usage import list_model_usage
from app.schemas.trial_policy import TrialPolicyChange, TrialPolicyResponse
from app.services.trial_policy import read_policy, change_policy
from app.schemas.platform_models import PlatformModelChange, PlatformModelPolicy
from app.services.platform_models import read_model_policy, update_model_policy
from app.schemas.commercial_health import CommercialHealthReport
from app.services.commercial_health import inspect_commercial_health

router = APIRouter(
    dependencies=[Depends(require_commercial_operations), Depends(require_operator)]
)


@router.get("/commercial-health", response_model=CommercialHealthReport)
def commercial_health(db: Session = Depends(get_db)) -> CommercialHealthReport:
    return inspect_commercial_health(db)


@router.get("/model-policy", response_model=PlatformModelPolicy)
def model_policy(db: Session = Depends(get_db)) -> PlatformModelPolicy:
    return read_model_policy(db)


@router.put("/model-policy", response_model=PlatformModelPolicy)
def save_model_policy(
    payload: PlatformModelChange,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> PlatformModelPolicy:
    result = update_model_policy(db, operator, payload)
    db.commit()
    return result


@router.get("/model-usage", response_model=list[ModelUsageResponse])
async def model_usage(
    project_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[ModelUsageResponse]:
    return await list_model_usage(project_id, offset, limit)


@router.get("/trial-policy", response_model=TrialPolicyResponse)
def trial_policy(db: Session = Depends(get_db)) -> TrialPolicyResponse:
    return read_policy(db)


@router.patch("/trial-policy", response_model=TrialPolicyResponse)
def update_trial_policy(
    payload: TrialPolicyChange,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> TrialPolicyResponse:
    result = change_policy(db, operator, payload)
    db.commit()
    return result


@router.get("/tasks", response_model=list[TaskResponse])
def tasks(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[BillingJob]:
    return list(
        db.scalars(
            select(BillingJob)
            .order_by(BillingJob.available_at.desc(), BillingJob.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.post("/tasks/{identifier}/retry", response_model=TaskResponse)
def retry(
    identifier: UUID,
    payload: RetryTask,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> BillingJob:
    job = retry_task(db, operator, identifier, payload)
    db.commit()
    return job


@router.get("/audits", response_model=list[AuditResponse])
def audits(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[BillingAudit]:
    return list(
        db.scalars(
            select(BillingAudit)
            .order_by(BillingAudit.created_at.desc(), BillingAudit.id)
            .offset(offset)
            .limit(limit)
        )
    )
