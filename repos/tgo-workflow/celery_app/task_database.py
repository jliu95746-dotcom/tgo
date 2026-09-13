"""Keep async database connections inside the event loop of one Celery task."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


@asynccontextmanager
async def workflow_task_session() -> AsyncIterator[AsyncSession]:
    # Each task calls asyncio.run; pooled asyncpg connections must not cross loops.
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()
