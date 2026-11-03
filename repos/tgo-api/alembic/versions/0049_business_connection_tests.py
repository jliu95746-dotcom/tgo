"""Persist business connection configuration tests and recovery receipts.

Revision ID: 0049_business_connection_tests
Revises: 0048_business_connections
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0049_business_connection_tests"
down_revision = "0048_business_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_business_connection_tests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("api_business_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("api_staff.id", ondelete="SET NULL")
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("authorization_revision", sa.Integer(), nullable=False),
        sa.Column("resource_revision", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt", postgresql.JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "project_id",
            "connection_id",
            "request_id",
            name="uq_connection_test_request",
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','cancelled')",
            name="ck_connection_test_status",
        ),
        sa.CheckConstraint("mode IN ('http','demo')", name="ck_connection_test_mode"),
        sa.CheckConstraint(
            "operation IN ('order_query','logistics_query')",
            name="ck_connection_test_operation",
        ),
    )
    op.create_index(
        "ix_api_business_connection_tests_project_id",
        "api_business_connection_tests",
        ["project_id"],
    )
    op.create_index(
        "ix_api_business_connection_tests_connection_id",
        "api_business_connection_tests",
        ["connection_id"],
    )


def downgrade() -> None:
    # A historical report must not disappear during a code rollback.
    exists = op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM api_business_connection_tests)")
    )
    if exists:
        raise RuntimeError(
            "Business connection test receipts exist; preserve them when rolling back code"
        )
    op.drop_index(
        "ix_api_business_connection_tests_connection_id",
        table_name="api_business_connection_tests",
    )
    op.drop_index(
        "ix_api_business_connection_tests_project_id",
        table_name="api_business_connection_tests",
    )
    op.drop_table("api_business_connection_tests")
