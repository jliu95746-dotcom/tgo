"""Shared, bounded registration throttling for all API workers."""

from hashlib import sha256

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings

REGISTRATION_WINDOW_SECONDS = 600
REGISTRATION_MAX_ATTEMPTS = 5
INCREMENT_ATTEMPTS = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


async def limit_registration(client_host: str) -> None:
    if not settings.REDIS_URL:
        raise HTTPException(503, "Registration is temporarily unavailable")
    host_key = sha256(client_host.encode("utf-8")).hexdigest()
    try:
        async with Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        ) as client:
            count = int(
                await client.eval(
                    INCREMENT_ATTEMPTS,
                    1,
                    f"registration:attempts:{host_key}",
                    REGISTRATION_WINDOW_SECONDS,
                )
            )
    except RedisError as exc:
        raise HTTPException(503, "Registration is temporarily unavailable") from exc
    if count > REGISTRATION_MAX_ATTEMPTS:
        raise HTTPException(
            429,
            "Too many registration attempts; please try again later",
            headers={"Retry-After": str(REGISTRATION_WINDOW_SECONDS)},
        )
