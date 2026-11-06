"""Authenticated, private-only native WeCom history and handoff bridge."""

from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.wecom_native import (
    NativeContextEvent,
    NativeContextResult,
    NativePendingResult,
    NativePlatformRequest,
)
from app.services.wecom_native_context import (
    mirror_native_event,
    pending_native_visitors,
)


def require_native_service(
    x_saas_service_token: str | None = Header(None),
) -> None:
    expected = settings.SAAS_INTERNAL_TOKEN
    if (
        expected is None
        or not x_saas_service_token
        or not compare_digest(
            expected.get_secret_value(),
            x_saas_service_token,
        )
    ):
        raise HTTPException(403, "内部服务身份无效")


router = APIRouter(dependencies=[Depends(require_native_service)])


@router.post("/event", response_model=NativeContextResult)
async def event(
    payload: NativeContextEvent, db: Annotated[Session, Depends(get_db)]
) -> NativeContextResult:
    return await mirror_native_event(db, payload)


@router.post("/pending", response_model=NativePendingResult)
def pending(
    payload: NativePlatformRequest, db: Annotated[Session, Depends(get_db)]
) -> NativePendingResult:
    return pending_native_visitors(db, payload.platform_id)
