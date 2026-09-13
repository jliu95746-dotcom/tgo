"""Keep assist correction intents until confirmed delivery and training capture.

Revision ID: 0035_delivery_training_context
Revises: 0034_staff_delivery_outbox
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0035_delivery_training_context"
down_revision = "0034_staff_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column("training_snapshot", JSONB(), nullable=True),
    )
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column(
            "training_status", sa.String(20), nullable=False, server_default="none"
        ),
    )
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column("training_error", sa.String(100), nullable=True),
    )
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column(
            "training_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column(
            "training_next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_check_constraint(
        "ck_staff_delivery_training",
        "api_staff_message_deliveries",
        "training_status IN ('none','pending','saved','unavailable')",
    )
    op.create_index(
        "ix_staff_delivery_training_recovery",
        "api_staff_message_deliveries",
        ["training_status", "training_next_retry_at"],
    )


def downgrade() -> None:
    # Export/drain pending corrections before an explicitly requested downgrade.
    op.drop_index(
        "ix_staff_delivery_training_recovery", table_name="api_staff_message_deliveries"
    )
    op.drop_constraint(
        "ck_staff_delivery_training", "api_staff_message_deliveries", type_="check"
    )
    for name in [
        "training_next_retry_at",
        "training_attempts",
        "training_error",
        "training_status",
        "training_snapshot",
    ]:
        op.drop_column("api_staff_message_deliveries", name)
