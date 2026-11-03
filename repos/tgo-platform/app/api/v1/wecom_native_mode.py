"""Private, tenant-scoped handback of a native human consultation."""

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.billing_receipts import require_service
from app.db.base import get_db
from app.db.models import Platform
from app.domain.services.listeners.wecom_listener import WeComPlatformConfig
from app.domain.services.wecom_native import (
    NativeBridge,
    NativeChannelConfig,
    NativeQueueConflict,
)


class NativeHandbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    platform_id: UUID
    external_userid: str = Field(min_length=1, max_length=128)


class NativeHandbackResult(BaseModel):
    service_state: Literal[0, 1, 4]


@dataclass
class HandbackPlatform:
    id: UUID
    project_id: UUID
    api_key: str | None
    cfg: NativeChannelConfig


router = APIRouter(prefix="/wecom-native", dependencies=[Depends(require_service)])


@router.post("/handback", response_model=NativeHandbackResult)
async def handback(
    payload: NativeHandbackRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> NativeHandbackResult:
    platform = await db.scalar(
        select(Platform).where(
            Platform.id == payload.platform_id,
            Platform.project_id == payload.project_id,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Platform.type == "wecom",
        )
    )
    if platform is None:
        raise HTTPException(404, "企微客服渠道不存在")
    config = WeComPlatformConfig(**(platform.config or {}))
    if not config.native_handoff_enabled or not config.native_open_kfid:
        raise HTTPException(409, "企微原生接待尚未启用")
    entry = HandbackPlatform(
        id=platform.id,
        project_id=platform.project_id,
        api_key=platform.api_key,
        cfg=config,
    )
    try:
        async with NativeBridge(entry) as bridge:
            state = await bridge.handback(payload.external_userid)
        return NativeHandbackResult.model_validate(state.model_dump())
    except NativeQueueConflict:
        raise HTTPException(
            409, "请先在企业微信接入并结束排队会话，再切换模式"
        ) from None
    except (httpx.HTTPError, RuntimeError, ValueError, RedisError):
        raise HTTPException(503, "企微原生接待尚未释放，请稍后重试") from None
