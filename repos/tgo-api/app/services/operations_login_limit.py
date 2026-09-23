"""Shared operator login limits; no raw email or IP in Redis keys."""

from hashlib import sha256

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.services.registration_limit import INCREMENT_ATTEMPTS


async def limit_operator_login(client_host: str, email: str) -> None:
    if not settings.REDIS_URL:
        raise TGOAPIException("登录服务暂不可用", "AUTH_UNAVAILABLE", status_code=503)
    try:
        async with Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        ) as client:
            for dimension, value, maximum in (
                ("host", client_host, 20),
                ("email", email.strip().lower(), 10),
            ):
                digest = sha256(value.encode("utf-8")).hexdigest()
                count = int(
                    await client.eval(
                        INCREMENT_ATTEMPTS,
                        1,
                        f"ops:login:{dimension}:{digest}",
                        600,
                    )
                )
                if count > maximum:
                    raise TGOAPIException(
                        "登录尝试过多，请 10 分钟后重试",
                        "RATE_LIMITED",
                        status_code=429,
                    )
    except RedisError as exc:
        raise TGOAPIException(
            "登录服务暂不可用",
            "AUTH_UNAVAILABLE",
            status_code=503,
        ) from exc
