"Signed personal WeChat session authorization and short-lived bridge \
presence."

from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from redis.asyncio import Redis
from redis import Redis as SyncRedis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Platform, Visitor
from app.services.im_access import authenticate_im_actor
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import (
    build_visitor_channel_id,
    parse_visitor_channel_id,
)
from app.utils.wechat_local import is_filehelper_identity, is_scoped_identity


class ScopedWechatTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str = Field(max_length=110)
    keywords: list[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=80
            ),
        ]
    ] = Field(
        default_factory=list,
        max_length=30,
    )

    @property
    def is_group(self) -> bool:
        return ":group:" in self.identity

    def allows_ai(self, text: str, mentioned: bool) -> bool:
        return (
            not self.is_group
            or mentioned
            or any(
                keyword.casefold() in text.casefold()
                for keyword in self.keywords
            )
        )


class WeChatLocalScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_tag: str = Field(pattern=r"^[a-f0-9]{16}$")
    targets: list[ScopedWechatTarget] = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_targets(self) -> "WeChatLocalScope":
        identities = set()
        for target in self.targets:
            if (
                not is_scoped_identity(target.identity)
                or not target.identity.startswith(
                    f"wechat-local:{self.account_tag}:"
                )
                or target.identity in identities
                or (target.keywords and not target.is_group)
            ):
                raise ValueError("个人微信授权范围无效")
            identities.add(target.identity)
        return self


class WeChatLocalHeartbeat(BaseModel):
    channel_id: str = Field(min_length=1, max_length=64)


class WeChatLocalScopeReceipt(BaseModel):
    accepted: bool = True


class WeChatLocalStatus(BaseModel):
    connected: bool = False
    last_seen_at: datetime | None = None
    scoped_enabled: bool = False


class WeChatLocalPairRequest(BaseModel):
    challenge: str = Field(pattern=r"^[a-f0-9]{64}$")


class WeChatLocalPairCode(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


class WeChatLocalPairExchange(WeChatLocalPairCode):
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


class WeChatLocalPairCredential(BaseModel):
    platform_api_key: str = Field(repr=False)


def require_bridge_visitor(
    db: Session,
    platform: Platform,
    channel_id: str | None,
    channel_type: int | None,
    credentials: HTTPAuthorizationCredentials | None,
    *,
    scoped: bool = False,
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
        or not (
            is_filehelper_identity(actor.platform_open_id)
            or (scoped and is_scoped_identity(actor.platform_open_id))
        )
    ):
        raise HTTPException(403, "个人微信会话范围不匹配")
    return actor


def _redis_key(platform: Platform) -> str:
    api_key = platform.api_key
    if not api_key:
        raise HTTPException(401, "个人微信渠道凭据不可用")
    digest = sha256(api_key.encode()).hexdigest()
    return f"tgo:wechat-local:{platform.id}:{digest}"


def _scoped_enabled(platform: Platform) -> bool:
    return (
        settings.WECHAT_LOCAL_SCOPED_ENABLED
        or platform.id in settings.WECHAT_LOCAL_SCOPED_PLATFORM_IDS
    )


def _pairing_key(platform: Platform, code: str, challenge: str) -> str:
    if not _scoped_enabled(platform):
        raise HTTPException(403, "个人微信接待范围功能仍在验收")
    if not platform.is_active or not platform.api_key:
        raise HTTPException(404, "个人微信渠道不可用")
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信连接暂不可用")
    return (
        f'{_redis_key(platform)}'
        f':pair:'
        f"{sha256(code.encode('ascii')).hexdigest()}"
        f':'
        f'{challenge}'
    )


async def issue_pairing_code(platform: Platform, challenge: str) -> str:
    code = token_urlsafe(32)
    key = _pairing_key(platform, code, challenge)
    redis_url = settings.REDIS_URL
    if not redis_url:
        raise HTTPException(503, "个人微信连接暂不可用")
    try:
        async with Redis.from_url(redis_url, socket_timeout=2) as redis:
            if not await redis.set(key, "1", ex=120, nx=True):
                raise HTTPException(503, "请重新确认连接这台电脑")
    except (RedisError, OSError) as exc:
        raise HTTPException(503, "个人微信连接暂不可用") from exc
    return code


async def exchange_pairing_code(
    platform: Platform, code: str, verifier: str
) -> str:
    challenge = sha256(verifier.encode("ascii")).hexdigest()
    key = _pairing_key(platform, code, challenge)
    api_key = platform.api_key
    if not isinstance(api_key, str) or not api_key:
        raise HTTPException(404, "个人微信渠道不可用")
    redis_url = settings.REDIS_URL
    if not redis_url:
        raise HTTPException(503, "个人微信连接暂不可用")
    try:
        async with Redis.from_url(redis_url, socket_timeout=2) as redis:
            if await redis.getdel(key) is None:
                raise HTTPException(400, "连接授权已过期或无效，请重新启动工具")
    except (RedisError, OSError) as exc:
        raise HTTPException(503, "个人微信连接暂不可用") from exc
    return api_key


async def publish_bridge_scope(
    platform: Platform, scope: WeChatLocalScope
) -> None:
    if not _scoped_enabled(platform):
        raise HTTPException(403, "个人微信接待范围功能仍在验收")
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信授权范围暂不可用")
    try:
        async with Redis.from_url(
            settings.REDIS_URL, socket_timeout=2
        ) as redis:
            key = _redis_key(platform)
            async with redis.pipeline(transaction=True) as pipeline:
                pipeline.set(key + ":scope", scope.model_dump_json(), ex=45)
                pipeline.set(
                    key + ":scoped-presence",
                    datetime.now(UTC).isoformat(),
                    ex=45,
                )
                await pipeline.execute()
    except (RedisError, OSError) as exc:
        raise HTTPException(503, "个人微信授权范围暂不可用") from exc


async def require_scoped_target(
    platform: Platform, identity: str
) -> ScopedWechatTarget:
    if not _scoped_enabled(platform):
        raise HTTPException(403, "个人微信接待范围功能仍在验收")
    if not is_scoped_identity(identity):
        raise HTTPException(403, "个人微信会话范围不匹配")
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信授权范围暂不可用")
    try:
        async with Redis.from_url(
            settings.REDIS_URL, socket_timeout=2
        ) as redis:
            raw = await redis.get(_redis_key(platform) + ":scope")
        scope = WeChatLocalScope.model_validate_json(raw) if raw else None
    except (RedisError, OSError, ValidationError) as exc:
        raise HTTPException(503, "个人微信授权范围暂不可用") from exc
    if scope:
        for target in scope.targets:
            if target.identity == identity:
                return target
    raise HTTPException(403, "个人微信会话未授权或已暂停")


def require_current_bridge_identity(
    platform: Platform, identity: str | None
) -> None:
    (
        'Recheck the live lease for every signed IM, history and file '
        'operation.'
    )
    if is_filehelper_identity(identity):
        return
    if not is_scoped_identity(identity):
        raise HTTPException(403, "个人微信会话范围不匹配")
    if not _scoped_enabled(platform):
        raise HTTPException(403, "个人微信接待范围功能仍在验收")
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信授权范围暂不可用")
    try:
        with SyncRedis.from_url(
            settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2
        ) as redis:
            raw = redis.get(_redis_key(platform) + ":scope")
        scope = WeChatLocalScope.model_validate_json(raw) if raw else None
    except (RedisError, OSError, ValidationError) as exc:
        raise HTTPException(503, "个人微信授权范围暂不可用") from exc
    if scope is not None and any(
        target.identity == identity for target in scope.targets
    ):
        return
    raise HTTPException(403, "个人微信会话未授权或已暂停")


async def authorize_bridge_visitor(
    db: Session,
    platform: Platform,
    channel_id: str | None,
    channel_type: int | None,
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[Visitor, ScopedWechatTarget | None]:
    actor = require_bridge_visitor(
        db, platform, channel_id, channel_type, credentials, scoped=True
    )
    target = None
    if not is_filehelper_identity(actor.platform_open_id):
        target = await require_scoped_target(
            platform, actor.platform_open_id or ""
        )
    return actor, target


async def bridge_status(
    platform: Platform, *, heartbeat: bool = False
) -> WeChatLocalStatus:
    if not platform.is_active or not platform.api_key:
        return WeChatLocalStatus()
    if not settings.REDIS_URL:
        raise HTTPException(503, "个人微信连接状态暂不可用")
    key = _redis_key(platform)
    scoped = _scoped_enabled(platform)
    try:
        async with Redis.from_url(
            settings.REDIS_URL, socket_timeout=2
        ) as redis:
            if heartbeat:
                now = datetime.now(UTC)
                await redis.set(key, now.isoformat(), ex=45)
                return WeChatLocalStatus(
                    connected=True, last_seen_at=now, scoped_enabled=scoped
                )
            values = (
                await redis.mget(key, key + ":scoped-presence")
                if scoped
                else [await redis.get(key)]
            )
    except (RedisError, OSError) as exc:
        raise HTTPException(503, "个人微信连接状态暂不可用") from exc
    timestamps = [
        datetime.fromisoformat(value.decode("ascii"))
        for value in values
        if isinstance(value, bytes)
    ]
    if not timestamps:
        return WeChatLocalStatus(scoped_enabled=scoped)
    return WeChatLocalStatus(
        connected=True,
        last_seen_at=max(timestamps),
        scoped_enabled=scoped,
    )
