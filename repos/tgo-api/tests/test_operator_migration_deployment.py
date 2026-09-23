"""Run guarded deployment against a private PostgreSQL schema and rollback."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

from app.services.operator_migration import (
    BASE_REVISION,
    TARGET_REVISION,
    apply_operator_revision,
)


@pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in PostgreSQL deployment test",
)
@pytest.mark.parametrize(
    "case",
    [
        "success",
        "wrong_version",
        "existing_table",
        "no_version",
    ],
)
def test_guarded_operator_deployment(case):
    url = os.environ["SAAS_TEST_DATABASE_URL"].replace(
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
    )
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "operator_deploy_" + uuid4().hex
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}"')
                )
                connection.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(128))"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO alembic_version VALUES ('other-service')"
                    )
                )
                connection.execute(
                    text("CREATE TABLE existing_company (value INTEGER)")
                )
                connection.execute(
                    text("INSERT INTO existing_company VALUES (42)")
                )
                if case != "no_version":
                    connection.execute(
                        text(
                            "CREATE TABLE api_alembic_version "
                            "(version_num VARCHAR(128) PRIMARY KEY)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO api_alembic_version "
                            "VALUES (:revision)"
                        ),
                        {
                            "revision": (
                                "0001_wrong"
                                if case == "wrong_version"
                                else BASE_REVISION
                            )
                        },
                    )
                if case == "existing_table":
                    connection.execute(
                        text(
                            "CREATE TABLE api_platform_operators (id INTEGER)"
                        )
                    )
                if case == "success":
                    savepoint = connection.begin_nested()
                    assert apply_operator_revision(connection) is True
                    assert apply_operator_revision(connection) is False
                    assert (
                        connection.scalar(
                            text("SELECT version_num FROM api_alembic_version")
                        )
                        == TARGET_REVISION
                    )
                    savepoint.rollback()
                    assert not inspect(connection).has_table(
                        "api_platform_operators"
                    )
                    assert (
                        connection.scalar(
                            text("SELECT version_num FROM api_alembic_version")
                        )
                        == BASE_REVISION
                    )
                    assert apply_operator_revision(connection) is True
                else:
                    with pytest.raises(ValueError):
                        apply_operator_revision(connection)
                assert (
                    connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                    == "other-service"
                )
                assert (
                    connection.scalar(
                        text("SELECT value FROM existing_company")
                    )
                    == 42
                )
                assert outer.is_active
            finally:
                outer.rollback()
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_namespace "
                        "WHERE nspname=:schema"
                    ),
                    {"schema": schema},
                )
                == 0
            )
    finally:
        engine.dispose()
