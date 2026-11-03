"""Bound unauthenticated ingress cost without retaining remote IP text."""

import hashlib
from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from app.core.config import settings

_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return count
"""


async def admit_public_webhook(remote_host: str) -> None:
    if not settings.REDIS_URL:
        raise HTTPException(503, "外部通知限流服务未配置。")
    digest = hashlib.sha256(remote_host.encode("utf-8")).hexdigest()
    client = Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
    )
    try:
        count = await client.eval(
            _INCREMENT, 1, "workflow:webhook-edge:v1:" + digest
        )
    except (RedisError, OSError, TimeoutError):
        raise HTTPException(503, "外部通知限流服务不可用。") from None
    finally:
        await client.aclose()
    if not isinstance(count, int) or count > 120:
        raise HTTPException(
            429, "外部通知请求过于频繁，请稍后重试。", headers={"Retry-After": "60"}
        )
