"""Atomic, tenant-scoped reply ownership and the final publication fence."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections.abc import Callable
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

from app.core.config import settings
from app.schemas.ai_runs import ReplyFailure, ReplyRun
from app.schemas.reply_phase import ReplyPhaseIdentity

TERMINAL = {"cancelled", "completed", "failed"}
Mutation = Callable[[ReplyRun | None], ReplyRun | None]


class RegistryConflict(Exception):
    """Another execution already owns this message."""


class RegistryUnavailable(Exception):
    """Control storage is unavailable; never assume permission to publish."""


class RunRegistry:
    async def get(self, project_id: str, client_msg_no: str) -> ReplyRun | None:
        raise NotImplementedError

    async def _mutate(
        self, project_id: str, client_msg_no: str, change: Mutation
    ) -> ReplyRun | None:
        raise NotImplementedError

    async def start(self, item: ReplyRun) -> ReplyRun:
        def change(previous: ReplyRun | None) -> ReplyRun:
            if previous is not None:
                raise RegistryConflict("Reply already registered")
            return item

        result = await self._mutate(item.project_id, item.client_msg_no, change)
        assert result is not None
        return result

    async def _owned(self, item: ReplyRun, change: Mutation) -> ReplyRun | None:
        def guarded(previous: ReplyRun | None) -> ReplyRun | None:
            if previous is None or previous.generation != item.generation:
                return None
            return change(previous)

        return await self._mutate(item.project_id, item.client_msg_no, guarded)

    async def heartbeat(self, item: ReplyRun) -> ReplyRun | None:
        return await self._owned(item, lambda previous: previous)

    async def start_phase(
        self, item: ReplyRun, phase: ReplyPhaseIdentity
    ) -> ReplyRun | None:
        if (phase.project_id, phase.client_msg_no, phase.generation) != (
            item.project_id,
            item.client_msg_no,
            item.generation,
        ):
            raise RegistryConflict("AI phase does not belong to this reply")

        def change(previous: ReplyRun | None) -> ReplyRun | None:
            if previous is None or previous.status != "active":
                return previous
            if previous.phase is not None and not previous.phase_ended:
                if previous.phase == phase:
                    return previous
                raise RegistryConflict("Another AI phase is still running")
            return previous.model_copy(update={"phase": phase, "phase_ended": False})

        return await self._owned(item, change)

    async def end_phase(self, phase: ReplyPhaseIdentity) -> ReplyRun | None:
        def change(previous: ReplyRun | None) -> ReplyRun | None:
            if (
                previous is None
                or previous.generation != phase.generation
                or previous.phase != phase
            ):
                return None
            result = previous.model_copy(update={"phase_ended": True})
            if (
                previous.status == "failed"
                and previous.failure_reason == "upstream_stop_unconfirmed"
            ):
                result = result.model_copy(
                    update={"status": "cancelled", "failure_reason": None}
                )
            return result

        return await self._mutate(phase.project_id, phase.client_msg_no, change)

    async def request_cancel(self, item: ReplyRun) -> ReplyRun | None:
        def change(previous: ReplyRun | None) -> ReplyRun | None:
            if previous is not None and previous.status == "active":
                return previous.model_copy(update={"status": "cancel_requested"})
            return previous

        return await self._owned(item, change)

    async def begin_publication(self, item: ReplyRun) -> ReplyRun | None:
        def change(previous: ReplyRun | None) -> ReplyRun | None:
            if previous is not None and previous.status == "active":
                return previous.model_copy(update={"status": "publishing"})
            return previous

        return await self._owned(item, change)

    async def finish(
        self,
        item: ReplyRun,
        status: Literal["cancelled", "completed", "failed"],
        failure_reason: ReplyFailure | None = None,
    ) -> ReplyRun | None:
        def change(previous: ReplyRun | None) -> ReplyRun | None:
            if previous is None or previous.status in TERMINAL:
                return previous
            if status == "completed" and previous.status != "publishing":
                return previous
            if status == "cancelled" and previous.status != "cancel_requested":
                return previous
            if (
                status == "failed"
                and failure_reason == "upstream_stop_unconfirmed"
                and previous.status == "cancel_requested"
                and previous.phase is not None
                and previous.phase_ended
            ):
                return previous.model_copy(
                    update={"status": "cancelled", "failure_reason": None}
                )
            return previous.model_copy(
                update={"status": status, "failure_reason": failure_reason}
            )

        return await self._owned(item, change)


class InMemoryRunRegistry(RunRegistry):
    """Single-process fallback; tests use their own independent instance."""

    def __init__(self, ttl_seconds: float = 60) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], tuple[ReplyRun, float]] = {}
        self._lock = asyncio.Lock()

    def _prune(self) -> None:
        now = time.monotonic()
        for key, (_, expiry) in list(self._entries.items()):
            if expiry <= now:
                self._entries.pop(key, None)

    async def get(self, project_id: str, client_msg_no: str) -> ReplyRun | None:
        async with self._lock:
            self._prune()
            stored = self._entries.get((project_id, client_msg_no))
            return stored[0] if stored else None

    async def _mutate(
        self, project_id: str, client_msg_no: str, change: Mutation
    ) -> ReplyRun | None:
        async with self._lock:
            self._prune()
            key = (project_id, client_msg_no)
            stored = self._entries.get(key)
            result = change(stored[0] if stored else None)
            if result is not None:
                ttl = 900 if result.status in TERMINAL else self._ttl
                self._entries[key] = (result, time.monotonic() + ttl)
            return result


class RedisRunRegistry(RunRegistry):
    """WATCH transactions serialize stop/publication across API workers."""

    def __init__(self, redis_url: str, ttl_seconds: float = 60) -> None:
        self._ttl = max(1, math.ceil(ttl_seconds))
        self._redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    @staticmethod
    def _key(project_id: str, client_msg_no: str) -> str:
        identity = (project_id + "\0" + client_msg_no).encode()
        return "tgo:reply_runs:v2:" + hashlib.sha256(identity).hexdigest()

    @staticmethod
    def _decode(raw: object, project_id: str, client_msg_no: str) -> ReplyRun | None:
        if raw is None:
            return None
        if not isinstance(raw, (str, bytes)):
            raise RegistryUnavailable("Invalid reply registry payload")
        try:
            item = ReplyRun.model_validate_json(raw)
        except ValueError as exc:
            raise RegistryUnavailable("Invalid reply registry payload") from exc
        if item.project_id != project_id or item.client_msg_no != client_msg_no:
            raise RegistryUnavailable("Reply registry identity mismatch")
        return item

    async def get(self, project_id: str, client_msg_no: str) -> ReplyRun | None:
        try:
            raw = await self._redis.get(self._key(project_id, client_msg_no))
            return self._decode(raw, project_id, client_msg_no)
        except RedisError as exc:
            raise RegistryUnavailable("Reply registry unavailable") from exc

    async def _mutate(
        self, project_id: str, client_msg_no: str, change: Mutation
    ) -> ReplyRun | None:
        key = self._key(project_id, client_msg_no)
        try:
            for _ in range(10):
                try:
                    async with self._redis.pipeline(transaction=True) as pipe:
                        await pipe.watch(key)
                        previous = self._decode(
                            await pipe.get(key), project_id, client_msg_no
                        )
                        result = change(previous)
                        if result is None:
                            return None
                        ttl = 900 if result.status in TERMINAL else self._ttl
                        pipe.multi()
                        pipe.set(key, result.model_dump_json(), ex=ttl)
                        await pipe.execute()
                        return result
                except WatchError:
                    continue
        except RedisError as exc:
            raise RegistryUnavailable("Reply registry unavailable") from exc
        raise RegistryUnavailable("Reply registry contention")

    async def close(self) -> None:
        await self._redis.aclose()


run_registry: RunRegistry = (
    RedisRunRegistry(settings.REDIS_URL)
    if settings.REDIS_URL
    else InMemoryRunRegistry()
)
