"""Private signed-byte ingress, reached through the public API gateway."""

import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks import require_internal_key
from app.core.logging import logger
from app.database import get_db
from app.schemas.webhook_delivery import WebhookAccepted
from app.services.webhook_http import read_signed_delivery
from app.services.webhook_ingress import accept_delivery

router = APIRouter(dependencies=[Depends(require_internal_key)])


@router.post("/{hook_id}", response_model=WebhookAccepted, status_code=202)
async def receive_webhook(
    hook_id: UUID,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> WebhookAccepted:
    body, headers, content_type = await read_signed_delivery(request)
    receipt = await accept_delivery(
        db, hook_id, body=body, headers=headers, content_type=content_type
    )
    response.headers["Cache-Control"] = "no-store"
    if receipt.status == "pending":
        from celery_app.webhook_tasks import dispatch_webhook_delivery_task

        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    dispatch_webhook_delivery_task.apply_async,
                    args=(str(receipt.delivery_id),),
                    task_id="webhook-source:" + str(receipt.delivery_id),
                    retry=False,
                ),
                timeout=2,
            )
        except Exception as error:
            logger.warning(
                "Webhook source broker acknowledgement uncertain: %s",
                type(error).__name__,
            )
    return receipt
