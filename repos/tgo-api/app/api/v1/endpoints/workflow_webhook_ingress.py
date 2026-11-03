"""Public HMAC deliveries; staff and service identities come from elsewhere."""

from uuid import UUID
from fastapi import APIRouter, HTTPException, Request, Response

from app.core.config import settings
from app.schemas.webhook_delivery import WebhookAccepted
from app.services.public_webhook_rate import admit_public_webhook
from app.services.webhook_http import read_signed_delivery
from app.services.workflow_client import workflow_client

router = APIRouter()


@router.post("/{hook_id}", response_model=WebhookAccepted, status_code=202)
async def receive_webhook(
    hook_id: UUID, request: Request, response: Response
) -> WebhookAccepted:
    if not settings.WEBHOOK_EXECUTION_ENABLED:
        raise HTTPException(503, "外部通知接收尚未开放。")
    await admit_public_webhook(
        request.client.host if request.client else "unknown"
    )
    body, headers, content_type = await read_signed_delivery(request)
    receipt = await workflow_client.accept_webhook(
        hook_id, body, headers, content_type
    )
    response.headers["Cache-Control"] = "no-store"
    return receipt
