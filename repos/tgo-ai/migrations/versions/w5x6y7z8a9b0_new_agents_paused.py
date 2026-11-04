"""Pause new rows by default; retain every existing employee's state."""

from alembic import op
import sqlalchemy as sa

revision = "w5x6y7z8a9b0"
down_revision = "v4w5x6y7z8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ai_agents", "is_active", existing_type=sa.Boolean(),
                    existing_nullable=False, server_default=sa.false())


def downgrade() -> None:
    op.alter_column("ai_agents", "is_active", existing_type=sa.Boolean(),
                    existing_nullable=False, server_default=sa.true())
