"""Index tenant-scoped business query audit trace lookup.

Revision ID: 0005
Revises: 0004
"""

from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_pg_business_query_audit_tenant_request_created",
            "pg_business_query_audit",
            ["tenant_id", "request_id", "created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_pg_business_query_audit_tenant_request_created",
            table_name="pg_business_query_audit",
            postgresql_concurrently=True,
        )
