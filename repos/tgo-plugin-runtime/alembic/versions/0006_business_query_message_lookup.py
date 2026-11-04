"""Index trusted message-scoped formal business query audits.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pg_business_query_audit",
        sa.Column("source_message_digest", sa.String(64), nullable=True),
    )
    op.add_column(
        "pg_business_query_audit",
        sa.Column("response_message_digest", sa.String(64), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_pg_business_query_audit_message_created",
            "pg_business_query_audit",
            ["tenant_id", "source_message_digest", "response_message_digest", "created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    has_message_links = op.get_bind().execute(sa.text(
        "SELECT 1 FROM pg_business_query_audit "
        "WHERE source_message_digest IS NOT NULL "
        "OR response_message_digest IS NOT NULL LIMIT 1"
    )).first()
    if has_message_links is not None:
        raise RuntimeError("Export or retain message-scoped business query audits before downgrade")
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_pg_business_query_audit_message_created",
            table_name="pg_business_query_audit",
            postgresql_concurrently=True,
        )
    op.drop_column("pg_business_query_audit", "response_message_digest")
    op.drop_column("pg_business_query_audit", "source_message_digest")
