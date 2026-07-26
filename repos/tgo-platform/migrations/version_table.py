"""Compatibility helpers for the platform Alembic version table."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def ensure_platform_version_table_capacity(connection: Connection) -> None:
    """Ensure symbolic revision names longer than Alembic's default 32 fit."""
    if connection.dialect.name != "postgresql":
        return

    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS pt_alembic_version ("
            "version_num VARCHAR(128) NOT NULL, "
            "CONSTRAINT pt_alembic_version_pkc PRIMARY KEY (version_num)"
            ")"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE pt_alembic_version "
            "ALTER COLUMN version_num TYPE VARCHAR(128)"
        )
    )
