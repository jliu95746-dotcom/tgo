"""Scoped, deduplicated application reminders; never part of customer delivery."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import JsonValue
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
NotificationKind = Literal["handoff", "queued", "assist"]


class AppNotificationConfig(Protocol):
    app_notification_enabled: bool
    app_notification_users: list[str]
    app_notification_base_url: str
    corp_id: str
    app_secret: str
    agent_id: str


def notification_url(base: str, visitor_id: UUID) -> str:
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Notification base URL must be an HTTPS origin")
    return base.rstrip("/") + f"/chat/251/{visitor_id}-vtr"


class AppNotifier:
    def __init__(self, platform_id: UUID, config: AppNotificationConfig, redis: Redis):
        self.config = config
        self.redis = redis
        self.prefix = f"wecom:app-notify:{platform_id}"

    async def _send(
        self, user: str, card: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        from app.api.wecom_utils import wecom_get_access_token, wecom_send_app_message

        # Application messages require the application Secret, never the KF Secret.
        token = await wecom_get_access_token(
            self.config.corp_id, self.config.app_secret
        )
        return await wecom_send_app_message(
            token,
            user,
            self.config.agent_id,
            "textcard",
            card,
            duplicate_check_interval=14400,
            timeout=10,
            enable_duplicate_check=True,
        )

    async def deliver(
        self, kind: NotificationKind, visitor_id: UUID, event_id: str, users: list[str]
    ) -> None:
        if not getattr(self.config, "app_notification_enabled", False):
            return
        for user in set(users) & set(self.config.app_notification_users):
            if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", user) or user == "@all":
                continue
            try:
                await self._deliver_one(kind, visitor_id, event_id, user)
            except (httpx.HTTPError, RuntimeError, ValueError, RedisError):
                # Provider failure must not retry a customer response or undo assignment.
                logger.warning("[WECOM_APP_NOTIFY] Reminder deferred; kind=%s", kind)

    async def _deliver_one(
        self, kind: NotificationKind, visitor_id: UUID, event_id: str, user: str
    ) -> None:
        digest = hashlib.sha256(
            f"{kind}:{visitor_id}:{event_id}:{user}".encode()
        ).hexdigest()
        sent = self.prefix + ":sent:" + digest
        retry = self.prefix + ":retry:" + digest
        cooldown = self.prefix + f":cooldown:{kind}:{visitor_id}:{user}"
        lock = self.redis.lock(self.prefix + ":lock:" + digest, timeout=30)
        if not await lock.acquire(blocking=False):
            return
        try:
            if await self.redis.exists(sent) or await self.redis.exists(retry):
                return
            if kind == "assist" and await self.redis.exists(cooldown):
                return  # Latest unread source remains pending and will be retried.
            titles = {
                "handoff": "客服会话已分配人工",
                "queued": "客服会话正在排队",
                "assist": "辅助模式待确认回复",
            }
            descriptions = {
                "handoff": "会话已分配给你，请在企业微信「工作台 → 微信客服」接待。点击下方可查看完整记录。",
                "queued": "当前暂无可接待人员，会话正在等待分配。请检查接待状态及容量。",
                "assist": "有顾客新消息待处理。请查看或生成 AI 草稿，修改后确认发送。",
            }
            card: dict[str, JsonValue] = {
                "title": titles[kind],
                "description": descriptions[kind]
                + f'<div class="gray">提醒编号 {digest[:12]}</div>',
                "url": notification_url(
                    self.config.app_notification_base_url, visitor_id
                ),
                "btntxt": "查看会话",
            }
            try:
                receipt = await self._send(user, card)
                if (
                    receipt.get("errcode") != 0
                    or not receipt.get("msgid")
                    or receipt.get("invaliduser")
                    or receipt.get("unlicenseduser")
                ):
                    raise RuntimeError(
                        "Application message was not accepted for recipient"
                    )
            except (httpx.HTTPError, RuntimeError, ValueError):
                await self.redis.set(retry, "1", ex=60)
                raise
            await self.redis.set(sent, str(receipt["msgid"]), ex=7776000)
            if kind == "assist":
                await self.redis.set(cooldown, "1", ex=60)
            logger.info(
                "[WECOM_APP_NOTIFY] Accepted; kind=%s event=%s", kind, digest[:12]
            )
        finally:
            await lock.release()

    async def native_state(self, visitor_id: UUID, state: int, employee: str) -> None:
        if not getattr(self.config, "app_notification_enabled", False):
            return
        episode_key = self.prefix + ":episode:" + str(visitor_id)
        if state not in {2, 3}:
            await self.redis.delete(episode_key)
            return
        await self.redis.set(episode_key, str(uuid4()), nx=True, ex=7776000)
        episode = await self.redis.get(episode_key)
        if episode:
            await self.deliver(
                "handoff" if state == 3 else "queued",
                visitor_id,
                episode,
                [employee] if state == 3 else self.config.app_notification_users,
            )
