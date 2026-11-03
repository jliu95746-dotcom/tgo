"""Dispatch lease migration preserves old approvals and active continuations."""

import importlib.util
import io
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def migration():
    path = Path(__file__).parents[2] / "migrations/versions/0008_resume_dispatch_lease.py"
    spec = importlib.util.spec_from_file_location("workflow_resume_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_dispatch_migration_preserves_legacy_approved_request():
    revision = migration()
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE wf_workflow_executions "
                "(id VARCHAR PRIMARY KEY, status VARCHAR NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE wf_approval_requests "
                "(id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, "
                "execution_id VARCHAR NOT NULL, status VARCHAR NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO wf_workflow_executions (id, status) "
                "VALUES ('run-a', 'pending_resume')"
            ))
            connection.execute(text(
                "INSERT INTO wf_approval_requests "
                "(id, project_id, execution_id, status) "
                "VALUES ('approval-a', 'project-a', 'run-a', 'approved')"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
                state = connection.execute(text(
                    "SELECT resume_dispatch_attempts, last_resume_dispatch_at "
                    "FROM wf_approval_requests WHERE id='approval-a'"
                )).one()
                assert state == (0, None)
                with pytest.raises(RuntimeError, match="approved runs await resume"):
                    revision.downgrade()
                connection.execute(text(
                    "UPDATE wf_workflow_executions SET status='completed' "
                    "WHERE id='run-a'"
                ))
                revision.downgrade()
                columns = connection.execute(text(
                    "PRAGMA table_info(wf_approval_requests)"
                )).all()
                assert "resume_dispatch_attempts" not in {column[1] for column in columns}
    finally:
        engine.dispose()


def test_resume_dispatch_migration_compiles_postgresql_sql():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration().upgrade()
    sql = output.getvalue()
    assert "last_resume_dispatch_at" in sql
    assert "resume_dispatch_attempts" in sql
    assert "ix_wf_approval_resume_dispatch" in sql
