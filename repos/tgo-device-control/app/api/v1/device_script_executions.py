"""Signed AI session routes; management credentials cannot start scripts."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.device_auth import require_service_principal
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_script_execution import ScriptExecuteBody
from app.schemas.device_script_receipt import ScriptExecutionRead
from app.services.device_script_execution import ScriptExecutionService

router = APIRouter()


@router.post(
    "/{device_id}/script-executions/{execution_id}",
    response_model=ScriptExecutionRead,
)
async def execute_script(
    device_id: UUID,
    execution_id: UUID,
    body: ScriptExecuteBody,
    principal: DeviceServicePrincipal = Depends(require_service_principal),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptExecutionRead:
    return await ScriptExecutionService(db).execute(
        principal, device_id, execution_id, body
    )


@router.get(
    "/{device_id}/script-executions/{execution_id}",
    response_model=ScriptExecutionRead,
)
async def get_script_receipt(
    device_id: UUID,
    execution_id: UUID,
    principal: DeviceServicePrincipal = Depends(require_service_principal),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptExecutionRead:
    return await ScriptExecutionService(db).read(
        principal, device_id, execution_id
    )
