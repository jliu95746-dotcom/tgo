"""Signed filehelper session authorization and short-lived bridge presence."""

from datetime import UTC, datetime
from hashlib import sha256

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Platform, Visitor
from app.services.im_access import authenticate_im_actor
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id, parse_visitor_channel_id
from app.utils.wechat_local import is_filehelper_identity


class WeChatLocalHeartbeat(BaseModel):
    channel_id: str = Field(min_length=1, max_length=64)


class WeChatLocalStatus(BaseModel):
    connected: bool = False
    last_seen_at: datetime | None = None


def require_bridge_visitor(
    db: Session,
    platform: Platform,
    channel_id: str | None,
    channel_type: int | None,
    credentials: HTTPAuthorizationCredentials | None,
) -> Visitor:
    if (
        channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE
        or not channel_id
        or not isinstance(credentials, HTTPAuthorizationCredentials)
        or credentials.scheme.lower() != "bearer"
        or not 0 < len(credentials.credentials) <= 8192
    ):
        raise HTTPException(401, "个人微信凭据无效")
    try:
        visitor_id = parse_visitor_channel_id(channel_id)
    except ValueError as exc:
        raise HTTPException(401, "个人微信会话无效") from exc
    if channel_id != build_visitor_channel_id(visitor_id):
        raise HTTPException(401, "个人微信会话无效")
    actor = authenticate_im_actor(db, channel_id, credentials.credentials)
    if (
        not isinstance(actor, Visitor)
        or actor.platform_id != platform.id
        or actor.project_id != platform.project_id
        or not is_filehelper_identity(actor.platform_open_id)
    ):
        raise HTTPException(403, "个人微信会话范围不匹配")
    return actor


async def bridge_status(platform: Platform, *, heartbeat: bool = False) -> WeChatLocalStatus:
    if not platform.is_active or not platform.api_key:
        return WeChatLocalStatus()
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信连接状态暂不可用")
    digest = sha256(platform.api_key.encode()).hexdigest()
    key = f"tgo:wechat-local:{platform.id}:{digest}"
    try:
        async with Redis.from_url(settings.REDIS_URL, socket_timeout=2) as redis:
            if heartbeat:
                now = datetime.now(UTC)
                await redis.set(key, now.isoformat(), ex=45)
                return WeChatLocalStatus(connected=True, last_seen_at=now)
            value = await redis.get(key)
    except (RedisError, OSError) as exc:
        raise HTTPException(503, "个人微信连接状态暂不可用") from exc
    if not isinstance(value, bytes):
        return WeChatLocalStatus()
    return WeChatLocalStatus(
        connected=True, last_seen_at=datetime.fromisoformat(value.decode("ascii")),
    )
