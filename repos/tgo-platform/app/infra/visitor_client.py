from __future__ import annotations
import json
import logging
from typing import Optional

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.domain.entities import VisitorInfo
from redis import asyncio as aioredis  # type: ignore

logger = logging.getLogger("visitor_client")


def channel_registration_url(base_url: str | None = None) -> str:
    value = base_url or settings.api_internal_service_url
    if not value:
        raise RuntimeError("请配置 API_INTERNAL_SERVICE_URL 后接入服务端渠道")
    return value.rstrip("/") + "/internal/channel-visitors/register"


def channel_registration_headers() -> dict[str, str]:
    token = settings.saas_internal_token
    if token is None or not token.get_secret_value():
        raise RuntimeError("请配置内部渠道服务凭据")
    return {"X-SaaS-Service-Token": token.get_secret_value()}


class VisitorRegisterPayload(BaseModel):
    """Payload for visitor registration."""

    platform_api_key: str
    project_id: str
    platform_type: str
    platform_open_id: str
    nickname: str | None = None
    avatar_url: str | None = None


class VisitorService:
    """Service to register and cache visitor info against tgo-api via Redis only.

    - Redis is mandatory for caching. No in-memory fallback.
    - Any Redis-related errors are allowed to propagate to the caller (fail fast).
    """

    def __init__(
        self,
        base_url: str | None = None,
        redis_url: str | None = None,
        cache_ttl_seconds: int = 24 * 60 * 60,
    ) -> None:
        self._base_url = base_url or settings.api_internal_service_url
        if not self._base_url:
            raise RuntimeError("请配置 API_INTERNAL_SERVICE_URL 后接入服务端渠道")
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=settings.request_timeout_seconds
        )
        self._cache_ttl = int(cache_ttl_seconds)
        self._redis_url = redis_url or settings.redis_url
        self._redis = None

    async def _ensure_redis(self) -> None:
        if self._redis is not None:
            return
        if not self._redis_url:
            raise RuntimeError("Redis URL not configured")
        if not aioredis:
            raise RuntimeError(
                "redis asyncio client not available; please install 'redis' package"
            )
        self._redis = aioredis.from_url(
            self._redis_url, encoding="utf-8", decode_responses=True
        )
        # Validate connection
        await self._redis.ping()

    @staticmethod
    def make_cache_key(
        project_id: str, platform_type: str, platform_open_id: str
    ) -> str:
        return (
            f"visitor:{project_id.lower()}:{platform_type.lower()}:{platform_open_id}"
        )

    async def get_cached(self, key: str) -> Optional[VisitorInfo]:
        await self._ensure_redis()
        data = await self._redis.get(key)  # type: ignore
        if not data:
            return None
        loaded = VisitorInfo.model_validate(json.loads(data))
        return VisitorInfo.model_validate(
            loaded.model_dump(include=set(VisitorInfo.model_fields))
        )

    async def set_cached(self, key: str, data: VisitorInfo) -> None:
        await self._ensure_redis()
        safe = data.model_dump_json(include=set(VisitorInfo.model_fields))
        await self._redis.set(key, safe, ex=self._cache_ttl)  # type: ignore

    async def register_or_get(
        self,
        platform_api_key: str,
        project_id: str,
        platform_type: str,
        platform_open_id: str,
        nickname: str | None = None,
        avatar_url: str | None = None,
    ) -> VisitorInfo:
        """Return cached or freshly registered visitor data.

        A cache miss uses the authenticated private API; public IDs never mint browser credentials.
        """
        key = self.make_cache_key(project_id, platform_type, platform_open_id)
        headers = channel_registration_headers()
        # Cached metadata is never authority after platform revocation or visitor deletion.
        # Resolve current source identity on the private server for every incoming operation.

        payload = VisitorRegisterPayload(
            platform_api_key=platform_api_key,
            project_id=project_id,
            platform_type=platform_type,
            platform_open_id=platform_open_id,
            nickname=nickname,
            avatar_url=avatar_url,
        )
        try:
            resp = await self._client.post(
                "/internal/channel-visitors/register",
                json=payload.model_dump(),
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            parsed = VisitorInfo.model_validate(data)
            visitor = VisitorInfo.model_validate(
                parsed.model_dump(include=set(VisitorInfo.model_fields))
            )
            # Cache successful result
            await self.set_cached(key, visitor)
            return visitor
        except Exception as e:
            logger.warning(
                "Channel visitor registration failed",
                extra={"platform_type": platform_type, "error_type": type(e).__name__},
            )
            raise

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass
