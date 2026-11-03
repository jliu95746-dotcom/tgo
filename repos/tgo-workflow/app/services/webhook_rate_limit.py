"""Atomic per-hook admission for authenticated, bounded notifications."""

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from app.config import settings
from app.services.event_rate_limit import _ADMIT_SCRIPT


async def admit_webhook(hook_id: str, event_key: str) -> None:
    client = Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
    )
    try:
        admitted = await client.eval(
            _ADMIT_SCRIPT,
            1,
            "workflow:webhook-rate:v1:" + hook_id,
            60000,
            60,
            event_key,
        )
    except (RedisError, OSError, TimeoutError):
        raise HTTPException(503, "外部通知限流服务不可用，请稍后重试。") from None
    finally:
        await client.aclose()
    if admitted != 1:
        raise HTTPException(
            429, "外部通知过于频繁，请稍后重试。", headers={"Retry-After": "60"}
        )
