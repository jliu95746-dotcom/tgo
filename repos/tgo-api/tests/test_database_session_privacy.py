"""Database failures preserve transactions without exposing payloads."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core import database

PRIVATE = "synthetic-private-database-value"


@pytest.mark.asyncio
async def test_health_failure_returns_false_without_logging_private_exception(
    monkeypatch,
    caplog,
):
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = RuntimeError(PRIVATE)

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    assert await database.check_db_health() is False
    assert PRIVATE not in caplog.text
    assert "RuntimeError" in caplog.text


def test_sync_dependency_rolls_back_and_reraises_without_logging_payload(
    monkeypatch,
    caplog,
):
    session = Mock(spec=Session)
    monkeypatch.setattr(database, "SessionLocal", Mock(return_value=session))
    dependency = database.get_db()
    assert next(dependency) is session
    problem = RuntimeError(PRIVATE)
    with pytest.raises(RuntimeError) as error:
        dependency.throw(problem)
    assert error.value is problem
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
    assert PRIVATE not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_async_failure_rolls_back_and_reraises_without_logging_payload(
    monkeypatch,
    caplog,
):
    session = AsyncMock(spec=AsyncSession)

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    dependency = database.get_async_db()
    assert await anext(dependency) is session
    problem = RuntimeError(PRIVATE)
    with pytest.raises(RuntimeError) as error:
        await dependency.athrow(problem)
    assert error.value is problem
    session.rollback.assert_awaited_once_with()
    session.close.assert_awaited_once_with()
    assert PRIVATE not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("engine_kind", ["sync", "async"])
def test_configured_engine_hides_bound_values_in_database_errors(engine_kind):
    configured = (
        database.sync_engine
        if engine_kind == "sync"
        else database.async_engine.sync_engine
    )
    engine = create_engine(
        "sqlite://", hide_parameters=configured.hide_parameters
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample (value TEXT UNIQUE)"))
            statement = text("INSERT INTO sample (value) VALUES (:value)")
            connection.execute(statement, {"value": PRIVATE})
            with pytest.raises(IntegrityError) as error:
                connection.execute(statement, {"value": PRIVATE})
            assert PRIVATE not in str(error.value)
    finally:
        engine.dispose()
