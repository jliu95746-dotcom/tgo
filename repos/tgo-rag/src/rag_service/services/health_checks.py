"""Bounded, read-only probes for RAG's database, vector and queue dependencies."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import text

from ..config import get_settings
from ..database import get_db_session
from ..schemas.observability import DependencyCheck, HealthChecks


async def measure_probe(
    probe: Callable[[], Awaitable[None]], timeout: float = 3.0
) -> DependencyCheck:
    started = time.perf_counter()
    error = None
    try:
        async with asyncio.timeout(timeout):
            await probe()
    except Exception as exc:
        # Never expose connection strings, SQL text or provider credentials.
        error = type(exc).__name__
    return DependencyCheck(
        status="unhealthy" if error else "healthy",
        response_time_ms=round((time.perf_counter() - started) * 1000, 2),
        error=error,
    )


async def check_database() -> None:
    async with get_db_session() as session:
        result = await session.execute(
            text(
                "SELECT to_regclass('rag_projects') IS NOT NULL "
                "AND to_regclass('rag_collections') IS NOT NULL "
                "AND to_regclass('rag_files') IS NOT NULL "
                "AND to_regclass('rag_file_documents') IS NOT NULL"
            )
        )
        if not result.scalar_one():
            raise RuntimeError("Required RAG tables are missing")


async def check_vector_database() -> None:
    async with get_db_session() as session:
        # Executes the actual pgvector distance operator, without customer vectors.
        result = await session.execute(
            text("SELECT '[1,0]'::vector <=> '[1,0]'::vector")
        )
        if abs(float(result.scalar_one())) > 1e-9:
            raise RuntimeError("Vector distance probe returned an invalid result")
        await session.execute(text("SELECT embedding FROM rag_file_documents LIMIT 0"))


async def check_redis() -> None:
    settings = get_settings()
    async with Redis.from_url(
        settings.redis_url,
        password=settings.redis_password,
        socket_connect_timeout=2,
        socket_timeout=2,
    ) as client:
        if not await client.ping():
            raise RuntimeError("Redis PING failed")


async def collect_health_checks() -> HealthChecks:
    started = time.perf_counter()
    database, redis, vector = await asyncio.gather(
        measure_probe(check_database),
        measure_probe(check_redis),
        measure_probe(check_vector_database),
    )
    return HealthChecks(
        database=database,
        redis=redis,
        vector_db=vector,
        total_check_time_ms=round((time.perf_counter() - started) * 1000, 2),
    )
