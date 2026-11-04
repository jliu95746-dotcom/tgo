"""Persist evidence that original visitor work stopped.

Revision ID: 0076_visitor_end_receipts
Revises: 0075_hidden_shadow_cancellation
"""

import sqlalchemy as sa
from alembic import op

revision = "0076_visitor_end_receipts"
down_revision = "0075_hidden_shadow_cancellation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_ai_interaction_runs",
        sa.Column("stop_confirmed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "api_visitors",
        sa.Column("im_disconnect_requested_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_ai_interaction_runs "
        "WHERE stop_confirmed_at IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Visitor stop receipts exist; archive before downgrade"
        )
    if connection.execute(sa.text(
        "SELECT 1 FROM api_visitors "
        "WHERE im_disconnect_requested_at IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Visitor IM receipts exist; archive before downgrade"
        )
    op.drop_column("api_visitors", "im_disconnect_requested_at")
    op.drop_column("api_ai_interaction_runs", "stop_confirmed_at")
