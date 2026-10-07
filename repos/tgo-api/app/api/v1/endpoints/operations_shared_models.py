"""Operator-only shared provider catalogue."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.operations import require_operator
from app.core.database import get_db
from app.models import AIProvider, ProjectAIConfig
from app.models.platform_operator import PlatformOperator
from app.schemas.shared_models import (
    SharedModelsChange,
    SharedModelsImport,
    SharedModelsView,
    SharedConnectionRequest,
    SharedConnectionResult,
)
from app.services.ai_provider_sync import sync_providers_with_retry
from app.services.project_ai_config_sync import sync_configs_with_retry
from app.services.operations_model_test import test_connection
from app.services.shared_models import (
    change_shared_models,
    import_shared_models,
    shared_models_view,
)

router = APIRouter(dependencies=[Depends(require_operator)])


@router.post("/shared-models/test", response_model=SharedConnectionResult)
async def check_connection(
    payload: SharedConnectionRequest, db: Session = Depends(get_db),
) -> SharedConnectionResult:
    return await test_connection(db, payload)


async def _publish(db: Session) -> None:
    providers = db.scalars(
        select(AIProvider).where(
            AIProvider.deleted_at.is_(None),
            AIProvider.sync_status == "pending",
        )
    ).all()
    providers_synced = True
    if providers:
        okay, error, _ = await sync_providers_with_retry(providers)
        providers_synced = okay
        for provider in providers:
            provider.last_synced_at = datetime.utcnow()
            provider.sync_status = "synced" if okay else "failed"
            provider.sync_error = None if okay else error
        db.commit()
    configs = db.scalars(
        select(ProjectAIConfig).where(
            ProjectAIConfig.deleted_at.is_(None),
            ProjectAIConfig.sync_status == "pending",
        )
    ).all()
    if configs and providers_synced:
        okay, error, _ = await sync_configs_with_retry(configs)
        for config in configs:
            config.last_synced_at = datetime.utcnow()
            config.sync_status = "synced" if okay else "failed"
            config.sync_error = None if okay else error
        db.commit()


@router.get("/shared-models", response_model=SharedModelsView)
def get_shared_models(db: Session = Depends(get_db)) -> SharedModelsView:
    return shared_models_view(db)


@router.post("/shared-models/import", response_model=SharedModelsView)
async def import_models(
    payload: SharedModelsImport,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> SharedModelsView:
    import_shared_models(db, operator, payload)
    db.commit()
    await _publish(db)
    return shared_models_view(db)


@router.put("/shared-models", response_model=SharedModelsView)
async def update_models(
    payload: SharedModelsChange,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> SharedModelsView:
    change_shared_models(db, operator, payload)
    db.commit()
    await _publish(db)
    return shared_models_view(db)
