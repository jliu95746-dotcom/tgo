"""Private read-only delivery receipts; business data stays in its owning service."""

from secrets import compare_digest
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import get_db
from app.db.models import Platform, WeComInbox


class DeliveryLookup(BaseModel):
    project_id: UUID
    platform_id: UUID
    source_message_id: str = Field(min_length=1, max_length=255)


class DeliveryReceipt(BaseModel):
    state: Literal["accepted", "pending", "unknown"]


def require_service(x_saas_service_token: str | None = Header(None)) -> None:
    if (
        settings.saas_internal_token is None
        or not x_saas_service_token
        or not compare_digest(
            x_saas_service_token, settings.saas_internal_token.get_secret_value()
        )
    ):
        raise HTTPException(403, "Invalid internal service identity")


router = APIRouter(prefix="/internal/billing", dependencies=[Depends(require_service)])


@router.post("/delivery", response_model=DeliveryReceipt)
async def delivery(
    payload: DeliveryLookup, db: AsyncSession = Depends(get_db)
) -> DeliveryReceipt:
    platform = await db.scalar(
        select(Platform).where(
            Platform.id == payload.platform_id,
            Platform.project_id == payload.project_id,
        )
    )
    if platform is None:
        raise HTTPException(404, "Platform not found")
    record = await db.scalar(
        select(WeComInbox).where(
            WeComInbox.platform_id == platform.id,
            WeComInbox.message_id == payload.source_message_id,
            WeComInbox.source_type == "wecom_kf",
        )
    )
    if record is not None and record.status == "completed" and record.ai_reply:
        return DeliveryReceipt(state="accepted")
    if record is not None and record.status in {"pending", "processing", "failed"}:
        return DeliveryReceipt(state="pending")
    return DeliveryReceipt(state="unknown")
