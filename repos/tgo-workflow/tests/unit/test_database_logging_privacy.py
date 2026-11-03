"""Query diagnostics must not persist bound customer input."""

import logging

import pytest
from sqlalchemy import text

from app import database
from app.config import Settings


def test_database_echo_defaults_to_disabled():
    assert Settings.model_fields["DATABASE_ECHO"].default is False


@pytest.mark.asyncio
async def test_explicit_query_diagnostics_hide_bound_values(caplog):
    engine = database.create_database_engine(
        "sqlite+aiosqlite:///:memory:", echo=True,
    )
    private_value = "synthetic-only-private-customer-input"
    try:
        with caplog.at_level(logging.INFO, logger="sqlalchemy.engine.Engine"):
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT :private_value"),
                    {"private_value": private_value},
                )
                assert result.scalar_one() == private_value
        assert "SELECT" in caplog.text
        assert private_value not in caplog.text
        assert engine.sync_engine.hide_parameters is True
    finally:
        await engine.dispose()
