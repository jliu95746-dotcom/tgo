"""Shared, atomic admission limit for signed internal workflow events."""

import hashlib
import json

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings


EVENTS_PER_MINUTE = 60
WINDOW_MILLISECONDS = 60_000
_ADMIT_SCRIPT = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) * 1000 + math.floor(tonumber(stamp[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - tonumber(ARGV[1]))
if redis.call('ZSCORE', KEYS[1], ARGV[3]) then
    return 1
end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]))
return 1
"""


async def admit_event(project_id: str, workflow_id: str, event_id: str) -> None:
    scope = json.dumps([project_id, workflow_id], separators=(",", ":"))
    scope_digest = hashlib.sha256(scope.encode()).hexdigest()
    redis_key = f"workflow:event-rate:v1:{scope_digest}"
    client = Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1,
    )
    try:
        admitted = await client.eval(
            _ADMIT_SCRIPT, 1, redis_key, WINDOW_MILLISECONDS,
            EVENTS_PER_MINUTE, event_id,
        )
    except (RedisError, OSError, TimeoutError):
        raise HTTPException(503, "内部事件限流服务不可用，请稍后重试。") from None
    finally:
        await client.aclose()
    if admitted != 1:
        raise HTTPException(429, "内部事件发送过于频繁，请稍后重试。",
                            headers={"Retry-After": "60"})
