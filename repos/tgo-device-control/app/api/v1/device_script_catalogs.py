"""Internal management discovery for device-local reviewed business scripts."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.device_auth import require_management_project
from app.schemas.device_script_policy import ScriptCatalog
from app.services.device_script_catalog import DeviceScriptCatalogService

router = APIRouter()


@router.get("/{device_id}/script-catalog", response_model=ScriptCatalog)
async def get_device_script_catalog(
    device_id: UUID,
    project_id: UUID = Depends(require_management_project),
    db: AsyncSession = Depends(get_async_db),
) -> ScriptCatalog:
    return await DeviceScriptCatalogService(db).get_catalog(
        project_id, device_id
    )
