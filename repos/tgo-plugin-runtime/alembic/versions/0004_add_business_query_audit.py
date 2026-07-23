"""add persistent business query audit

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pg_business_query_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "visitor_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "parameter_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pg_business_query_audit_tenant_id"),
        "pg_business_query_audit",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_pg_business_query_audit_tenant_created",
        "pg_business_query_audit",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pg_business_query_audit_tenant_created",
        table_name="pg_business_query_audit",
    )
    op.drop_index(
        op.f("ix_pg_business_query_audit_tenant_id"),
        table_name="pg_business_query_audit",
    )
    op.drop_table("pg_business_query_audit")
