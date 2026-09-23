"""Operator issuance and authenticated company redemption of trial codes."""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.company_membership import require_company_features
from app.api.v1.endpoints.operations import (
    require_commercial_operations,
    require_operator,
)
from app.core.database import get_db
from app.core.security import require_admin
from app.models import Staff
from app.models.platform_operator import PlatformOperator
from app.models.trial_activation_code import TrialActivationCode
from app.schemas.company_email import ActionResponse
from app.schemas.trial_activation import (
    TrialCodeIssue,
    TrialCodeRecord,
    TrialRedeemRequest,
)
from app.services.registration_limit import limit_registration
from app.services.trial_activation import issue_code, redeem_code

ops_router = APIRouter(dependencies=[Depends(require_commercial_operations)])
company_router = APIRouter(dependencies=[Depends(require_company_features)])


@ops_router.post("/trial-codes", response_model=TrialCodeIssue, status_code=201)
def create_trial_code(
    response: Response,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> TrialCodeIssue:
    code, record = issue_code(db, operator)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return TrialCodeIssue(
        **TrialCodeRecord.model_validate(record).model_dump(), code=code
    )


@ops_router.get("/trial-codes", response_model=list[TrialCodeRecord])
def list_trial_codes(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> list[TrialActivationCode]:
    return list(
        db.scalars(
            select(TrialActivationCode)
            .order_by(TrialActivationCode.created_at.desc(), TrialActivationCode.id)
            .limit(limit)
        )
    )


@company_router.post("/trial-activation", response_model=ActionResponse)
async def activate_trial(
    payload: TrialRedeemRequest,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> ActionResponse:
    await limit_registration(f"trial-redeem:{actor.project_id}")
    redeem_code(db, actor, payload.code.strip())
    db.commit()
    return ActionResponse(message="试用激活成功")
