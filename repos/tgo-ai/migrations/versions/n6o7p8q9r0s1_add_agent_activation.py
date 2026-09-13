"""Persist employee activation; retain existing employees as enabled."""
from alembic import op
import sqlalchemy as sa

revision = "n6o7p8q9r0s1"
down_revision = "m5n6o7p8q9r0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_agents",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("ai_agents", "is_active")
