"""Verify database checks and initialization against real PostgreSQL."""
import os

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import database

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL,
    reason="Disposable PostgreSQL database required",
)


@pytest.mark.asyncio
async def test_real_async_database_connection_is_reported_healthy(monkeypatch):
    assert TEST_URL is not None
    url = make_url(TEST_URL).set(drivername="postgresql+asyncpg")
    assert (url.database or "").startswith("tgo_scenarios_test_")
    engine = create_async_engine(url, hide_parameters=True)
    monkeypatch.setattr(
        database, "AsyncSessionLocal", async_sessionmaker(engine)
    )
    try:
        assert await database.check_db_health() is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_initialization_creates_tables_idempotently(monkeypatch):
    assert TEST_URL is not None
    url = make_url(TEST_URL).set(drivername="postgresql+asyncpg")
    assert (url.database or "").startswith("tgo_scenarios_test_")
    engine = create_async_engine(url, hide_parameters=True)
    monkeypatch.setattr(database, "async_engine", engine)
    try:
        await database.init_db()
        await database.init_db()
        async with engine.connect() as connection:
            names = await connection.run_sync(
                lambda sync: inspect(sync).get_table_names()
            )
        assert {
            "api_projects",
            "api_staff",
            "api_visitor_assignment_rules",
            "api_visitor_assignment_history",
        }.issubset(names)
    finally:
        await engine.dispose()
