"Project AI Config sync API for tgo-api -> tgo-ai \
(internal).\n\nInternal, unauthenticated endpoint to upsert per-project \
default AI model configs.\n"

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.responses import build_error_responses
from app.dependencies import get_db
from app.schemas.project_ai_config import (
    ProjectAIConfigResponse,
    ProjectAIConfigSyncRequest,
    ProjectAIConfigSyncResponse,
    ProjectAIConfigUpsert,
)
from app.services.project_ai_config_service import ProjectAIConfigService


router = APIRouter()


def get_service(db: AsyncSession = Depends(get_db)) -> ProjectAIConfigService:
    return ProjectAIConfigService(db)


@router.post(
    "/sync",
    response_model=ProjectAIConfigSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk upsert Project AI Configs",
    description=(
        (
            'Upsert project-level default AI model configs. Accepts a list '
            'of configs; each item uses project_id as the primary key. If a '
            'config exists it will be updated; otherwise created. Intended '
            'for internal service-to-service sync from tgo-api.'
        )
    ),
    responses=build_error_responses([400]),
)
async def sync_project_ai_configs(
    request: ProjectAIConfigSyncRequest,
    service: ProjectAIConfigService = Depends(get_service),
) -> ProjectAIConfigSyncResponse:
    items = await service.sync_configs(
        [c.model_dump() for c in request.configs]
    )
    return ProjectAIConfigSyncResponse(
        data=[ProjectAIConfigResponse.from_orm_model(i) for i in items]
    )


@router.put(
    "",
    response_model=ProjectAIConfigResponse,
    summary="Upsert a single Project AI Config",
    description=(
        (
            'Upsert a single project-level default AI model config. Uses '
            'project_id as the key. Returns 201 if created, 200 if updated. '
            'Intended for internal service-to-service sync from tgo-api.'
        )
    ),
    responses=build_error_responses([400]),
)
async def upsert_project_ai_config(
    payload: ProjectAIConfigUpsert,
    response: Response,
    service: ProjectAIConfigService = Depends(get_service),
) -> ProjectAIConfigResponse:
    # Determine if record exists to set proper status code

    existing = await service.get(payload.project_id)
    item = await service.upsert_config(**payload.model_dump())
    response.status_code = (
        status.HTTP_201_CREATED if existing is None else status.HTTP_200_OK
    )
    return ProjectAIConfigResponse.from_orm_model(item)
