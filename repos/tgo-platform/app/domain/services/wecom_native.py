"""Native WeCom handoff, fair assignment and human-message history mirroring."""

from __future__ import annotations

import logging
import re
import time
from typing import Literal, Protocol, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, JsonValue
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)


class NativeChannelConfig(Protocol):
    corp_id: str
    app_secret: str
    kf_secret: str
    native_handoff_enabled: bool
    native_open_kfid: str
    native_capacity: int
    agent_id: str
    app_notification_enabled: bool
    app_notification_users: list[str]
    app_notification_base_url: str


class NativePlatform(Protocol):
    id: UUID
    project_id: UUID
    api_key: str | None
    cfg: NativeChannelConfig


class NativeInbox(Protocol):
    open_kfid: str | None
    message_id: str
    raw_payload: dict[str, JsonValue] | None


class NativeServicer(BaseModel):
    userid: str = ""
    status: int = 1


class NativeState(BaseModel):
    service_state: Literal[0, 1, 2, 3, 4]
    servicer_userid: str = ""


class NativeContextResult(BaseModel):
    visitor_id: UUID
    service_mode: Literal["auto", "assist", "manual"]
    mirrored: bool = False


class NativeProviderError(RuntimeError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"WeCom native service returned errcode {code}")


class NativeQueueConflict(RuntimeError):
    """The provider cannot release a waiting-pool session through its API."""


class _CredentialLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "access_token=" in message:
            record.msg = re.sub(r"(access_token=)[^&\s\"']+", r"\1[REDACTED]", message)
            record.args = ()
        return True


def choose_candidates(
    people: list[NativeServicer],
    previous: str,
    cursor: int,
    loads: dict[str, int],
    capacity: int,
) -> list[str]:
    available = sorted(
        {
            person.userid
            for person in people
            if person.userid
            and person.status == 0
            and loads.get(person.userid, 0) < capacity
        }
    )
    if not available:
        return []
    offset = cursor % len(available)
    rotated = available[offset:] + available[:offset]
    if previous in rotated:
        rotated.remove(previous)
        rotated.insert(0, previous)
    return rotated


def _text(value: JsonValue | object) -> str:
    return value if isinstance(value, str) else ""


def native_content(message: dict[str, JsonValue]) -> str:
    kind = _text(message.get("msgtype"))
    body = message.get(kind)
    if kind == "text" and isinstance(body, dict):
        return _text(body.get("content"))[:8000]
    if kind == "link" and isinstance(body, dict):
        return "\n".join(_text(body.get(key)) for key in ["title", "desc", "url"])[
            :8000
        ]
    labels = {
        "image": "图片",
        "voice": "语音",
        "video": "视频",
        "file": "文件",
        "location": "位置",
        "miniprogram": "小程序",
        "business_card": "名片",
    }
    return "[" + labels.get(kind, kind or "消息") + "消息，内容需查看原消息]"


class NativeBridge:
    def __init__(self, platform: NativePlatform):
        self.platform = platform
        self.config = platform.cfg
        self.open_kfid = self.config.native_open_kfid
        self.prefix = f"wecom:native:{platform.id}:{self.open_kfid}"
        self.client: httpx.AsyncClient

    async def __aenter__(self):
        logger = logging.getLogger("httpx")
        if not any(isinstance(item, _CredentialLogFilter) for item in logger.filters):
            logger.addFilter(_CredentialLogFilter())
        self.client = httpx.AsyncClient(timeout=10, trust_env=False)
        return self

    async def __aexit__(self, *_args):
        await self.client.aclose()

    async def _redis(self):
        from app.api.wecom_utils import get_redis_client

        redis = await get_redis_client()
        if redis is None:
            raise RuntimeError("Native WeCom assignment requires Redis")
        return redis

    async def _provider(
        self, method: str, path: str, body: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        from app.api.wecom_utils import wecom_get_access_token
        from app.domain.services.wecom_credentials import resolve_wecom_kf_secret

        token = await wecom_get_access_token(
            self.config.corp_id,
            resolve_wecom_kf_secret(self.config.kf_secret, self.config.app_secret),
        )
        params = {"access_token": token}
        if method == "GET":
            params["open_kfid"] = self.open_kfid
        response = await self.client.request(
            method,
            "https://qyapi.weixin.qq.com/cgi-bin/kf/" + path,
            params=params,
            json=body if method != "GET" else None,
        )
        if response.status_code != 200:
            raise RuntimeError(f"WeCom native HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("Invalid WeCom native response")
        code = int(data.get("errcode", 0))
        if code:
            raise NativeProviderError(code)
        return cast(dict[str, JsonValue], data)

    async def _internal(
        self, path: str, body: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        base = settings.api_internal_service_url
        credential = settings.saas_internal_token
        if not base or credential is None:
            raise RuntimeError("Native WeCom internal transport is not configured")
        response = await self.client.post(
            base.rstrip("/") + "/internal/wecom-native/" + path,
            headers={"X-SaaS-Service-Token": credential.get_secret_value()},
            json=body,
        )
        if response.status_code != 200:
            raise RuntimeError(f"WeCom native history HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("Invalid native history response")
        return cast(dict[str, JsonValue], data)

    async def state(self, external: str) -> NativeState:
        return NativeState.model_validate(
            await self._provider(
                "POST",
                "service_state/get",
                {"open_kfid": self.open_kfid, "external_userid": external},
            )
        )

    async def _transition(
        self, external: str, state: int, employee: str = ""
    ) -> NativeState:
        body: dict[str, JsonValue] = {
            "open_kfid": self.open_kfid,
            "external_userid": external,
            "service_state": state,
        }
        if employee:
            body["servicer_userid"] = employee
        await self._provider("POST", "service_state/trans", body)
        actual = await self.state(external)
        if actual.service_state != state or (
            state == 3 and actual.servicer_userid != employee
        ):
            raise RuntimeError("WeCom native assignment was not confirmed")
        return actual

    async def mirror(
        self,
        external: str,
        *,
        kind: Literal["state", "customer", "staff"],
        state: NativeState,
        event_id: str,
        content: str = "",
        servicer_userid: str = "",
        source_type: str = "text",
        sent_at: int | None = None,
    ) -> NativeContextResult:
        payload: dict[str, JsonValue] = {
            "platform_id": str(self.platform.id),
            "external_userid": external,
            "kind": kind,
            "service_state": state.service_state,
            "event_id": event_id,
            "servicer_userid": servicer_userid or state.servicer_userid,
            "content": content,
            "source_type": source_type,
            "sent_at": sent_at,
        }
        result = NativeContextResult.model_validate(
            await self._internal("event", payload)
        )
        await self._track(external, state)
        if getattr(self.config, "app_notification_enabled", False):
            from app.domain.services.wecom_app_notifications import AppNotifier

            try:
                await AppNotifier(self.platform.id, self.config, await self._redis()).native_state(
                    result.visitor_id, state.service_state, state.servicer_userid
                )
            except (httpx.HTTPError, RuntimeError, ValueError, RedisError):
                logger.warning("[WECOM_APP_NOTIFY] State reminder will retry")
        return result

    async def _track(self, external: str, state: NativeState) -> None:
        redis = await self._redis()
        active_key = self.prefix + ":active:" + external
        previous = await redis.get(active_key)
        if previous and (state.service_state != 3 or previous != state.servicer_userid):
            await redis.zrem(self.prefix + ":load:" + previous, external)
            await redis.delete(active_key)
        if state.service_state == 3 and state.servicer_userid:
            await redis.zadd(
                self.prefix + ":load:" + state.servicer_userid, {external: time.time()}
            )
            await redis.set(active_key, state.servicer_userid, ex=172800)
            await redis.set(
                self.prefix + ":previous:" + external, state.servicer_userid, ex=7776000
            )
            await redis.zrem(self.prefix + ":queue", external)
        elif state.service_state == 4:
            await redis.zrem(self.prefix + ":queue", external)

    async def route(self, external: str) -> None:
        redis = await self._redis()
        lock = redis.lock(
            self.prefix + ":assignment-lock", timeout=45, blocking_timeout=1
        )
        if not await lock.acquire(blocking=False):
            return
        try:
            state = await self.state(external)
            if state.service_state in {3, 4}:
                await self.mirror(
                    external, kind="state", state=state, event_id="native-state"
                )
                return
            await redis.zadd(self.prefix + ":queue", {external: time.time()}, nx=True)
            people_data = await self._provider("GET", "servicer/list")
            raw_people = people_data.get("servicer_list")
            people = (
                [
                    NativeServicer.model_validate(item)
                    for item in raw_people
                    if isinstance(item, dict) and item.get("userid")
                ]
                if isinstance(raw_people, list)
                else []
            )
            loads: dict[str, int] = {}
            for person in people:
                key = self.prefix + ":load:" + person.userid
                await redis.zremrangebyscore(key, "-inf", time.time() - 172800)
                loads[person.userid] = int(await redis.zcard(key))
            previous = await redis.get(self.prefix + ":previous:" + external) or ""
            cursor = int(await redis.get(self.prefix + ":round-robin") or 0)
            candidates = choose_candidates(
                people, previous, cursor, loads, self.config.native_capacity
            )
            for employee in candidates:
                try:
                    assigned = await self._transition(external, 3, employee)
                except NativeProviderError:
                    # The provider also checks activation, capacity and current availability.
                    # A rejected employee is never reported as a successful assignment.
                    continue
                await redis.incr(self.prefix + ":round-robin")
                await self.mirror(
                    external, kind="state", state=assigned, event_id="native-assigned"
                )
                return
            if state.service_state != 2:
                state = await self._transition(external, 2)
            await self.mirror(
                external, kind="state", state=state, event_id="native-queued"
            )
        finally:
            await lock.release()

    async def handback(self, external: str) -> NativeState:
        """Release human ownership before the API commits an AI/assist mode.

        Do not call mirror here: the caller holds the visitor transaction lock.
        WeCom only allows human -> ended, then a new customer message starts AI.
        """
        redis = await self._redis()
        lock = redis.lock(
            self.prefix + ":assignment-lock", timeout=45, blocking_timeout=5
        )
        if not await lock.acquire(blocking=True):
            raise RuntimeError("Native assignment is busy; mode was not changed")
        try:
            state = await self.state(external)
            if state.service_state == 2:
                raise NativeQueueConflict("Waiting session must be accepted and ended")
            if state.service_state == 3:
                state = await self._transition(external, 4)
            await self._track(external, state)
            await redis.zrem(self.prefix + ":queue", external)
            return state
        finally:
            await lock.release()

    async def release(self, external: str) -> None:
        redis = await self._redis()
        lock = redis.lock(
            self.prefix + ":assignment-lock", timeout=45, blocking_timeout=1
        )
        if not await lock.acquire(blocking=False):
            return
        try:
            state = await self.state(external)
            if state.service_state in {1, 3}:
                state = await self._transition(external, 4)
            elif state.service_state == 0:
                # A customer has already started the next consultation.
                state = await self._transition(external, 1)
            elif state.service_state == 2:
                # The documented API has no direct waiting-pool -> ended edge.
                raise RuntimeError("Cannot release a waiting WeCom session via API")
            await self.mirror(
                external, kind="state", state=state, event_id="native-released"
            )
            await redis.zrem(self.prefix + ":queue", external)
        finally:
            await lock.release()

    async def reconcile(self) -> None:
        response = await self._internal(
            "pending", {"platform_id": str(self.platform.id)}
        )
        raw_visitors = response.get("visitors")
        if not isinstance(raw_visitors, list):
            raise TypeError("Invalid native pending visitors")
        redis = await self._redis()
        releases: set[str] = set()
        for row in raw_visitors:
            if isinstance(row, dict) and _text(row.get("external_userid")):
                external = _text(row["external_userid"])
                if row.get("release_requested") is True:
                    releases.add(external)
                await redis.zadd(
                    self.prefix + ":queue", {external: time.time()}, nx=True
                )
        waiting = await redis.zrange(self.prefix + ":queue", 0, 9)
        for external in waiting:
            try:
                if external in releases:
                    await self.release(external)
                    continue
                current = await self.mirror(
                    external,
                    kind="state",
                    state=await self.state(external),
                    event_id="native-reconcile",
                )
                if current.service_mode == "manual":
                    await self.route(external)
                else:
                    await redis.zrem(self.prefix + ":queue", external)
            except (httpx.HTTPError, RuntimeError, ValueError, RedisError) as exc:
                logger.warning(
                    "[WECOM_NATIVE] Assignment will retry: %s", type(exc).__name__
                )
        if getattr(self.config, "app_notification_enabled", False):
            from app.domain.services.wecom_app_notifications import AppNotifier

            notifier = AppNotifier(self.platform.id, self.config, redis)
            reminders = response.get("assist_notifications", [])
            if isinstance(reminders, list):
                for reminder in reminders:
                    if isinstance(reminder, dict):
                        await notifier.deliver(
                            "assist", UUID(_text(reminder.get("visitor_id"))),
                            _text(reminder.get("event_id")), self.config.app_notification_users,
                        )


async def _register(platform: NativePlatform, external: str, visitor_service) -> None:
    await visitor_service.register_or_get(
        platform_api_key=platform.api_key or "",
        project_id=str(platform.project_id),
        platform_type="wecom",
        platform_open_id=external,
    )


async def handle_native_record(
    platform: NativePlatform, record: NativeInbox, visitor_service
) -> bool:
    if not platform.cfg.native_handoff_enabled:
        return False
    message = (record.raw_payload or {}).get("kf_sync_msg")
    if not isinstance(message, dict):
        return False
    event = message.get("event")
    detail = event if isinstance(event, dict) else {}
    open_kfid = (
        _text(message.get("open_kfid"))
        or _text(detail.get("open_kfid"))
        or record.open_kfid
    )
    if open_kfid != platform.cfg.native_open_kfid:
        return False
    external = _text(message.get("external_userid")) or _text(
        detail.get("external_userid")
    )
    if (
        message.get("msgtype") == "event"
        and detail.get("event_type") != "session_status_change"
    ):
        return True  # System events are never treated as customers asking the AI a question.
    if not external:
        return True
    await _register(platform, external, visitor_service)
    async with NativeBridge(platform) as bridge:
        state = await bridge.state(external)
        if message.get("msgtype") == "event":
            await bridge.mirror(
                external, kind="state", state=state, event_id=record.message_id
            )
            return True
        if message.get("origin") == 5:
            await bridge.mirror(
                external,
                kind="staff",
                state=state,
                event_id=record.message_id,
                content=native_content(message),
                servicer_userid=_text(message.get("servicer_userid")),
                source_type=_text(message.get("msgtype")),
                sent_at=message.get("send_time")
                if isinstance(message.get("send_time"), int)
                else None,
            )
            return True
        current = await bridge.mirror(
            external, kind="state", state=state, event_id=record.message_id
        )
        if state.service_state in {2, 3} or current.service_mode == "manual":
            await bridge.mirror(
                external,
                kind="customer",
                state=state,
                event_id=record.message_id,
                content=native_content(message),
                source_type=_text(message.get("msgtype")),
                sent_at=message.get("send_time")
                if isinstance(message.get("send_time"), int)
                else None,
            )
            if state.service_state != 3:
                await bridge.route(external)
            return True
    return False


async def reconcile_native_handoffs(platform: NativePlatform) -> None:
    if platform.cfg.native_handoff_enabled:
        async with NativeBridge(platform) as bridge:
            await bridge.reconcile()
