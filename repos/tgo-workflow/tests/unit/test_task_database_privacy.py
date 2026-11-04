"""Even an SQL error from a task must conceal bound customer values."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.config import settings
from celery_app.task_database import workflow_task_session


@pytest.mark.asyncio
async def test_task_sql_exceptions_do_not_echo_bound_private_values(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:"
    )
    async with workflow_task_session() as db:
        await db.execute(text("CREATE TABLE proof (value TEXT UNIQUE)"))
        secret = "synthetic-private-customer-payload-not-for-logs"
        await db.execute(
            text("INSERT INTO proof VALUES (:value)"), {"value": secret}
        )
        with pytest.raises(IntegrityError) as caught:
            await db.execute(
                text("INSERT INTO proof VALUES (:value)"), {"value": secret}
            )
        assert secret not in str(caught.value)
