"""Preserve cancelled G4 registrations while releasing their active slot.

Revision ID: 0075_hidden_shadow_cancellation
Revises: 0074_hidden_shadow_seed_commitment
"""

import sqlalchemy as sa
from alembic import op

revision = "0075_hidden_shadow_cancellation"
down_revision = "0074_hidden_shadow_seed_commitment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_hidden_shadow_protocols",
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "api_hidden_shadow_protocols",
        sa.Column("cancelled_by", sa.Uuid()),
    )
    op.drop_index(
        "uq_hidden_protocol_active_project",
        table_name="api_hidden_shadow_protocols",
    )
    op.create_index(
        "uq_hidden_protocol_active_project", "api_hidden_shadow_protocols",
        ["project_id"], unique=True,
        postgresql_where=sa.text(
            "finalized_at IS NULL AND cancelled_at IS NULL"
        ),
        sqlite_where=sa.text(
            "finalized_at IS NULL AND cancelled_at IS NULL"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_hidden_shadow_protocols "
        "WHERE cancelled_at IS NOT NULL OR cancelled_by IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Cancelled registrations exist; archive them before downgrade"
        )
    op.drop_index(
        "uq_hidden_protocol_active_project",
        table_name="api_hidden_shadow_protocols",
    )
    op.drop_column("api_hidden_shadow_protocols", "cancelled_by")
    op.drop_column("api_hidden_shadow_protocols", "cancelled_at")
    op.create_index(
        "uq_hidden_protocol_active_project", "api_hidden_shadow_protocols",
        ["project_id"], unique=True,
        postgresql_where=sa.text("finalized_at IS NULL"),
        sqlite_where=sa.text("finalized_at IS NULL"),
    )
