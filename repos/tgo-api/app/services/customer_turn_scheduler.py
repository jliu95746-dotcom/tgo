"""Redis wakeups and renewable ownership; PostgreSQL remains the source of truth."""

import time
from uuid import UUID

from redis.asyncio import Redis
from app.core.config import settings


class TurnScheduler:
    queue = "customer-turns:due:v1"

    def __init__(self) -> None:
        self.redis = (
            Redis.from_url(settings.REDIS_URL, decode_responses=True,
                           socket_connect_timeout=1, socket_timeout=1)
            if settings.REDIS_URL
            else None
        )
        self.memory: dict[str, float] = {}
        self.locks: dict[str, tuple[str, float]] = {}

    async def enqueue(self, turn_id: UUID, due: float) -> None:
        if self.redis:
            await self.redis.zadd(self.queue, {str(turn_id): due})
        else:
            self.memory[str(turn_id)] = due

    async def due(self) -> list[UUID]:
        if self.redis:
            return [
                UUID(value)
                for value in await self.redis.zrangebyscore(
                    self.queue, "-inf", time.time(), start=0, num=32
                )
            ]
        return [
            UUID(key) for key, due in list(self.memory.items()) if due <= time.time()
        ][:32]

    async def acquire(self, turn_id: UUID, owner: str) -> bool:
        key = "customer-turns:lease:" + str(turn_id)
        if self.redis:
            return bool(await self.redis.set(key, owner, nx=True, ex=30))
        current = self.locks.get(key)
        if current and current[1] > time.monotonic():
            return False
        self.locks[key] = (owner, time.monotonic() + 30)
        return True

    async def renew(self, turn_id: UUID, owner: str) -> bool:
        key = "customer-turns:lease:" + str(turn_id)
        if self.redis:
            return bool(
                await self.redis.eval(
                    "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('expire',KEYS[1],30) else return 0 end",
                    1,
                    key,
                    owner,
                )
            )
        if self.locks.get(key, (None,))[0] != owner:
            return False
        self.locks[key] = (owner, time.monotonic() + 30)
        return True

    async def release(self, turn_id: UUID, owner: str) -> None:
        key = "customer-turns:lease:" + str(turn_id)
        if self.redis:
            await self.redis.eval(
                "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
                1,
                key,
                owner,
            )
        elif self.locks.get(key, (None,))[0] == owner:
            self.locks.pop(key, None)

    async def remove(self, turn_id: UUID) -> None:
        if self.redis:
            await self.redis.zrem(self.queue, str(turn_id))
        else:
            self.memory.pop(str(turn_id), None)

    async def remove_if_due(self, turn_id: UUID, due: float) -> None:
        if self.redis:
            await self.redis.eval(
                "if tonumber(redis.call('zscore',KEYS[1],ARGV[1]))==tonumber(ARGV[2]) then return redis.call('zrem',KEYS[1],ARGV[1]) else return 0 end",
                1,
                self.queue,
                str(turn_id),
                str(due),
            )
        elif self.memory.get(str(turn_id)) == due:
            self.memory.pop(str(turn_id), None)

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()
