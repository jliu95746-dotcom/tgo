"""Store merchant issuer policies and scoped customer session receipts.

Revision ID: 0050_business_customer_identity
Revises: 0049_business_connection_tests
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0050_business_customer_identity"
down_revision = "0049_business_connection_tests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_business_customer_identity_policies",
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
            "updated_by", sa.Uuid(), sa.ForeignKey("api_staff.id", ondelete="SET NULL")
        ),
        sa.Column("platform_ids", postgresql.JSONB(), nullable=False),
        sa.Column("signing_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("connection_configuration_revision", sa.Integer(), nullable=False),
        sa.Column(
            "max_session_seconds", sa.Integer(), nullable=False, server_default="900"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id", "connection_id", name="uq_customer_identity_policy_connection"
        ),
        sa.CheckConstraint(
            "revision >= 1 AND connection_configuration_revision >= 1 AND max_session_seconds BETWEEN 60 AND 3600",
            name="ck_customer_identity_policy_limits",
        ),
    )
    op.create_table(
        "api_business_customer_bindings",
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
            "policy_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_business_customer_identity_policies.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "visitor_id",
            sa.Uuid(),
            sa.ForeignKey("api_visitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "platform_id",
            sa.Uuid(),
            sa.ForeignKey("api_platforms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nonce", sa.Uuid(), nullable=False),
        sa.Column("proof_digest", sa.String(64), nullable=False),
        sa.Column("customer_ciphertext", sa.Text(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("connection_configuration_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("policy_id", "nonce", name="uq_customer_binding_nonce"),
        sa.CheckConstraint(
            "revision >= 1 AND policy_revision >= 1 AND connection_configuration_revision >= 1",
            name="ck_customer_binding_revisions",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_customer_binding_expiry"),
    )
    for table, columns in [
        ("api_business_customer_identity_policies", ["project_id", "connection_id"]),
        (
            "api_business_customer_bindings",
            ["project_id", "connection_id", "visitor_id"],
        ),
    ]:
        for column in columns:
            op.create_index("ix_" + table + "_" + column, table, [column])


def downgrade() -> None:
    for table in (
        "api_business_customer_bindings",
        "api_business_customer_identity_policies",
    ):
        if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM " + table + ")")):
            raise RuntimeError(
                "Customer identity records exist; preserve keys and receipts when rolling back code"
            )
    for table, columns in [
        (
            "api_business_customer_bindings",
            ["project_id", "connection_id", "visitor_id"],
        ),
        ("api_business_customer_identity_policies", ["project_id", "connection_id"]),
    ]:
        for column in columns:
            op.drop_index("ix_" + table + "_" + column, table_name=table)
        op.drop_table(table)
