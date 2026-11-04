"""Private management routes, separate from the signed delivery ingress."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.workflow_webhook import (
    WebhookChange,
    WebhookChangeInternal,
    WebhookReceipt,
    WebhookSave,
    WebhookSaveInternal,
    WebhookState,
)
from app.services import workflow_webhooks
from app.schemas.webhook_history import WebhookDeliveryList
from app.services.webhook_management_views import list_deliveries

router = APIRouter()


def require_internal_key(
    x_internal_api_key: str | None = Header(default=None),
) -> None:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise HTTPException(503, "外部触发内部身份核验未配置。")
    if not x_internal_api_key or not hmac.compare_digest(
        x_internal_api_key.encode(),
        key.get_secret_value().encode(),
    ):
        raise HTTPException(403, "外部触发内部身份无法确认。")


@router.get(
    "/{workflow_id}/webhook/deliveries",
    response_model=WebhookDeliveryList,
    dependencies=[Depends(require_internal_key)],
)
async def get_deliveries(
    workflow_id: str,
    response: Response,
    project_id: UUID = Query(...),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    db: AsyncSession = Depends(get_db),
) -> WebhookDeliveryList:
    response.headers["Cache-Control"] = "no-store"
    return await list_deliveries(db, workflow_id, project_id, limit, offset)


@router.get(
    "/{workflow_id}/webhook",
    response_model=WebhookState,
    dependencies=[Depends(require_internal_key)],
)
async def get_webhook(
    workflow_id: str,
    response: Response,
    project_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> WebhookState:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_webhooks.get_webhook(db, workflow_id, project_id)


@router.put(
    "/{workflow_id}/webhook",
    response_model=WebhookReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def save_webhook(
    workflow_id: str,
    request: WebhookSaveInternal,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> WebhookReceipt:
    response.headers["Cache-Control"] = "no-store"
    public = WebhookSave.model_validate(
        request.model_dump(include=set(WebhookSave.model_fields)),
    )
    return await workflow_webhooks.save_webhook(
        db,
        workflow_id,
        request.project_id,
        request.actor_staff_id,
        public,
    )


@router.post(
    "/{workflow_id}/webhook/disable",
    response_model=WebhookReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def disable_webhook(
    workflow_id: str,
    request: WebhookChangeInternal,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> WebhookReceipt:
    response.headers["Cache-Control"] = "no-store"
    public = WebhookChange.model_validate(
        request.model_dump(include=set(WebhookChange.model_fields)),
    )
    return await workflow_webhooks.disable_webhook(
        db,
        workflow_id,
        request.project_id,
        request.actor_staff_id,
        public,
    )


@router.post(
    "/{workflow_id}/webhook/rotate",
    response_model=WebhookReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def rotate_webhook(
    workflow_id: str,
    request: WebhookChangeInternal,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> WebhookReceipt:
    response.headers["Cache-Control"] = "no-store"
    public = WebhookChange.model_validate(
        request.model_dump(include=set(WebhookChange.model_fields)),
    )
    return await workflow_webhooks.rotate_webhook(
        db,
        workflow_id,
        request.project_id,
        request.actor_staff_id,
        public,
    )


@router.get(
    "/{workflow_id}/webhook/commands/{request_id}",
    response_model=WebhookReceipt,
    dependencies=[Depends(require_internal_key)],
)
async def get_command_receipt(
    workflow_id: str,
    request_id: UUID,
    response: Response,
    project_id: UUID = Query(...),
    actor_staff_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> WebhookReceipt:
    response.headers["Cache-Control"] = "no-store"
    return await workflow_webhooks.get_command_receipt(
        db,
        workflow_id,
        project_id,
        actor_staff_id,
        request_id,
    )
