"""Only the authenticated AI service can resolve an issued request context."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.business_trial_route import BusinessTrialRoute
from app.api.internal.endpoints.agent_activation import (
    require_activation_service,
)
from app.core.database import get_db
from app.schemas.business_execution_context import (
    BusinessContextCheck,
    BusinessRequestPrincipal,
)
from app.services.business_execution_context import (
    BusinessExecutionContextService,
)

router = APIRouter(route_class=BusinessTrialRoute)


@router.post(
    "/ai/business-execution/context-check",
    response_model=BusinessRequestPrincipal,
    dependencies=[Depends(require_activation_service)],
)
def verify_context(
    check: BusinessContextCheck, db: Session = Depends(get_db)
) -> BusinessRequestPrincipal:
    return BusinessExecutionContextService(db).verify(check)
