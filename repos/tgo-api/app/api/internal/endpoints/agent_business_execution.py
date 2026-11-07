"Formal business routes stay private and use the existing service \
identities."

from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.business_trial_route import BusinessTrialRoute
from app.api.internal.endpoints.agent_activation import (
    require_activation_service,
)
from app.api.internal.endpoints.business_connections import (
    require_business_adapter,
)
from app.core.database import get_db
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionCheck,
    BusinessExecutionReceipt,
)
from app.schemas.business_execution_resolution import (
    BusinessExecutionResolution,
)
from app.services.agent_business_execution_resolution import (
    query_execution,
    resolve_execution,
)

router = APIRouter(route_class=BusinessTrialRoute)


@router.post(
    "/ai/business-execution/query",
    response_model=BusinessExecutionReceipt,
    dependencies=[Depends(require_activation_service)],
)
async def query(
    command: BusinessExecutionCommand, db: Session = Depends(get_db)
) -> BusinessExecutionReceipt:
    return await query_execution(db, command)


@router.post(
    "/business-connections/{connection_id}/business-execution",
    response_model=BusinessExecutionResolution,
    dependencies=[Depends(require_business_adapter)],
)
async def resolve(
    connection_id: UUID,
    check: BusinessExecutionCheck,
    db: Session = Depends(get_db),
) -> BusinessExecutionResolution:
    return await resolve_execution(db, connection_id, check)
