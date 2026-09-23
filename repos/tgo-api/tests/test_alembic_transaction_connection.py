"""Run the actual migration environment in an isolated outer transaction."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings


@pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in PostgreSQL migration environment test",
)
def test_explicit_connection_keeps_migration_and_data_in_caller_transaction(
    monkeypatch,
):
    url = os.environ["SAAS_TEST_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    # An env.py that ignores the supplied connection must fail safely, never
    # attempt a migration against a configured customer database.
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql://synthetic:unused@127.0.0.1:1/never_used",
    )
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    schema = "alembic_scope_" + uuid4().hex
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}"')
                )
                connection.execute(
                    text("CREATE TABLE existing_company (value INTEGER)")
                )
                connection.execute(
                    text("INSERT INTO existing_company VALUES (42)")
                )
                connection.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(32))"
                    )
                )
                connection.execute(
                    text("INSERT INTO alembic_version VALUES ('0001_init')")
                )
                config.attributes["connection"] = connection
                # Stamp only this new disposable schema to exercise 0036->0037.
                command.stamp(config, "0036_retire_personal_wechat")
                command.upgrade(config, "0037_platform_operators")
                assert inspect(connection).has_table("api_platform_operators")
                assert (
                    connection.execute(
                        text("SELECT version_num FROM api_alembic_version")
                    ).scalar_one()
                    == "0037_platform_operators"
                )
                assert (
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "0001_init"
                )
                assert (
                    connection.execute(
                        text("SELECT value FROM existing_company")
                    ).scalar_one()
                    == 42
                )
                command.downgrade(config, "0036_retire_personal_wechat")
                assert not inspect(connection).has_table(
                    "api_platform_operators"
                )
                assert (
                    connection.execute(
                        text("SELECT version_num FROM api_alembic_version")
                    ).scalar_one()
                    == "0036_retire_personal_wechat"
                )
                assert (
                    connection.execute(
                        text("SELECT value FROM existing_company")
                    ).scalar_one()
                    == 42
                )
                assert outer.is_active
            finally:
                outer.rollback()
        with engine.connect() as check:
            assert (
                check.execute(
                    text(
                        "SELECT count(*) FROM pg_namespace "
                        "WHERE nspname=:schema"
                    ),
                    {"schema": schema},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
