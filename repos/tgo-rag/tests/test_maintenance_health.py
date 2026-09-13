"""Health probes use SQL aggregates, never load document or file contents."""

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from src.rag_service.tasks import maintenance


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [(0, 0, 0), (4, 17, 2)])
async def test_health_counts_use_aggregates(monkeypatch, counts):
    results = [Mock(scalar=Mock(return_value=value)) for value in (1, *counts)]
    db = Mock(execute=AsyncMock(side_effect=results))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(maintenance, "get_db_session", session)
    result = await maintenance._health_check_async()

    assert result["status"] == "healthy", result
    assert result["database"] == "connected"
    actual = (
        result["total_files"], result["total_documents"], result["stuck_tasks"]
    )
    assert actual == counts
    statements = [call.args[0] for call in db.execute.await_args_list]
    assert len(statements) == 4
    for statement in statements[1:]:
        sql = str(statement.compile(dialect=postgresql.dialect())).lower()
        assert sql.startswith("select count("), sql
    stuck_sql = str(statements[-1]).lower()
    assert "status" in stuck_sql and "updated_at" in stuck_sql
    threshold = next(
        value for value in statements[-1].compile().params.values()
        if isinstance(value, datetime)
    )
    assert threshold.tzinfo is not None
    for value in results:
        value.scalars.assert_not_called()


@pytest.mark.asyncio
async def test_unreachable_database_is_not_healthy(monkeypatch):
    @asynccontextmanager
    async def session():
        raise ConnectionError("isolated database unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(maintenance, "get_db_session", session)
    result = await maintenance._health_check_async()
    assert result["status"] == "unhealthy"
    assert result["database"] == "disconnected"
