"""Rehearse additive account migrations in a rolled-back PostgreSQL schema."""

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))

from app.core.database import sync_engine  # noqa: E402
from app.models import Project, Staff  # noqa: E402
from app.models.platform_operator import PlatformOperator  # noqa: E402


def migration(name):
    path = ROOT / "repos/tgo-api/alembic/versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    schema = "account_migration_" + uuid4().hex
    with sync_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            Project.__table__.create(connection)
            Staff.__table__.create(connection)
            PlatformOperator.__table__.create(connection)
            security = migration("0038_staff_account_security")
            email = migration("0039_company_email_trial")
            invitations = migration("0040_company_invitations")
            billing = migration("0041_billing_foundation")
            usage = migration("0042_ai_usage_ledger")
            reconciliation = migration("0043_billing_reconciliation")
            trial_policy = migration("0044_trial_policy")
            with Operations.context(MigrationContext.configure(connection)):
                # Reconstruct the pre-0038 staff table inside this private schema.
                security.downgrade()
                project_id, staff_id = uuid4(), uuid4()
                connection.execute(text(
                    "INSERT INTO api_projects (id,name,api_key,created_at,updated_at) "
                    "VALUES (:id,'synthetic company','synthetic-key',now(),now())"
                ), {"id": project_id})
                connection.execute(text(
                    "INSERT INTO api_staff "
                    "(id,project_id,username,password_hash,role,status,is_active,"
                    "service_paused,created_at,updated_at) VALUES "
                    "(:id,:project,'legacy-user','unused','admin','offline',"
                    "false,true,now(),now())"
                ), {"id": staff_id, "project": project_id})
                security.upgrade()
                email.upgrade()
                invitations.upgrade()
                billing.upgrade()
                usage.upgrade()
                reconciliation.upgrade()
                trial_policy.upgrade()
                row = connection.execute(text(
                    "SELECT account_enabled,token_version,email_verified_at,"
                    "is_active,service_paused FROM api_staff WHERE id=:id"
                ), {"id": staff_id}).one()
                assert tuple(row) == (True, 1, None, False, True)
                assert connection.scalar(text(
                    "SELECT count(*) FROM api_company_accounts"
                )) == 0
                print("PASS: old login preserved; reception unchanged; no trial enrolled")
                assert len(inspect(connection).get_columns("api_email_outbox")) == 11
                assert "api_ai_usage_movements" in inspect(connection).get_table_names()
                assert "api_billing_reconciliations" in inspect(connection).get_table_names()
                assert "api_trial_policies" in inspect(connection).get_table_names()
                trial_policy.downgrade()
                reconciliation.downgrade()
                usage.downgrade()
                billing.downgrade()
                invitations.downgrade()
                email.downgrade()
                security.downgrade()
                assert connection.scalar(text("SELECT count(*) FROM api_staff")) == 1
                assert "account_enabled" not in {
                    c["name"] for c in inspect(connection).get_columns("api_staff")
                }
                print("PASS: seven migrations upgrade and downgrade without losing old staff")
        finally:
            transaction.rollback()
            print("PASS: private schema rolled back; main schema unchanged")


if __name__ == "__main__":
    main()
