"""Exercise the actual operator migration without touching company records."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
from secrets import token_urlsafe
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import pytest


def test_operator_migration_upgrade_downgrade_keeps_existing_data():
    path = Path(__file__).parents[1] / "alembic/versions/0037_platform_operators.py"
    spec = spec_from_file_location("operator_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_company (name TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO existing_company VALUES ('existing')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            columns = {
                item["name"]
                for item in inspect(connection).get_columns("api_platform_operators")
            }
            assert {"email", "password_hash", "token_version", "is_active"} <= columns
            connection.execute(
                text(
                    "INSERT INTO api_platform_operators (id, email, name, password_hash) "
                    "VALUES ('sample', 'operator@example.com', 'operator', 'not-a-password')"
                )
            )
            assert connection.execute(
                text("SELECT is_active, token_version FROM api_platform_operators")
            ).one() == (1, 1)
            with pytest.raises(IntegrityError):
                connection.execute(
                    text("UPDATE api_platform_operators SET token_version = 0")
                )
            migration.downgrade()
            assert "api_platform_operators" not in inspect(connection).get_table_names()
            migration.upgrade()
        assert (
            connection.execute(text("SELECT name FROM existing_company")).scalar()
            == "existing"
        )
    engine.dispose()


@pytest.mark.skipif(not os.getenv("SAAS_TEST_DATABASE_URL"), reason="Opt-in PostgreSQL migration test")
def test_postgres_migration_and_operator_workflow_in_rolled_back_schema(monkeypatch):
    """Use a private transaction schema; all test records and DDL are rolled back."""
    from pydantic import SecretStr
    from app.core.config import settings
    from app.core.exceptions import TGOAPIException
    from app.models import Project, Staff
    from app.schemas.operations import OperatorCreateRequest
    from app.services.operations_auth import (
        authenticate_operator, create_operator, issue_operator_token, resolve_operator_token,
    )
    from app.services.operations_preview import preview_company_migration

    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "OPS_SECRET_KEY", SecretStr(token_urlsafe(40)))
    url = os.environ["SAAS_TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "saas_test_" + uuid4().hex
    path = Path(__file__).parents[1] / "alembic/versions/0037_platform_operators.py"
    spec = spec_from_file_location("operator_migration_pg", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()
                migration.upgrade()
            Project.__table__.create(connection)
            Staff.__table__.create(connection)
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                company = Project(name="测试企业", api_key=token_urlsafe(24))
                db.add(company)
                db.flush()
                db.add(Staff(project_id=company.id, username="saas-test-admin", role="admin", password_hash="unused"))
                db.commit()
                password = token_urlsafe(24)
                created = create_operator(db, OperatorCreateRequest(
                    email="test@example.com", name="测试运营", password=password,
                ))
                operator = authenticate_operator(db, "test@example.com", password)
                token = issue_operator_token(operator).access_token
                assert resolve_operator_token(db, token).id == created.id
                result = preview_company_migration(db, 20, 0)
                assert result.pagination.total == 1
                assert result.data[0].administrator_accounts == 1
                assert result.will_change_data is False
                operator.token_version += 1
                db.commit()
                with pytest.raises(TGOAPIException) as error:
                    resolve_operator_token(db, token)
                assert error.value.status_code == 401
        finally:
            transaction.rollback()
        assert connection.execute(text(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name=:name"
        ), {"name": schema}).scalar() == 0
    engine.dispose()
