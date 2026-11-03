"""Short-lived presence for the explicitly bound desktop bridge."""

from datetime import UTC, datetime
from hashlib import sha256

from fastapi import HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.models import Platform


class WeChatLocalStatus(BaseModel):
    connected: bool = False
    last_seen_at: datetime | None = None


def _key(platform: Platform) -> str:
    digest = sha256((platform.api_key or "").encode()).hexdigest()
    return f"tgo:wechat-local:{platform.id}:{digest}"


async def bridge_status(platform: Platform, *, heartbeat: bool = False) -> WeChatLocalStatus:
    if not platform.is_active or not platform.api_key:
        return WeChatLocalStatus()
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信连接状态暂不可用")
    try:
        async with Redis.from_url(settings.REDIS_URL, socket_timeout=2) as redis:
            if heartbeat:
                now = datetime.now(UTC)
                await redis.set(_key(platform), now.isoformat(), ex=45)
                return WeChatLocalStatus(connected=True, last_seen_at=now)
            value = await redis.get(_key(platform))
    except (RedisError, OSError) as exc:
        raise HTTPException(503, "个人微信连接状态暂不可用") from exc
    if not isinstance(value, bytes):
        return WeChatLocalStatus()
    return WeChatLocalStatus(
        connected=True, last_seen_at=datetime.fromisoformat(value.decode("ascii")),
    )
