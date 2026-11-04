"""Add owner-read trial revisions while retaining historical reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "t2u3v4w5x6y7"
down_revision = "s1t2u3v4w5x6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_agent_test_reports", sa.Column(
        "knowledge_snapshot", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=True,
    ))


def downgrade() -> None:
    # Removing this column discards revision evidence, but retains report history.
    op.drop_column("ai_agent_test_reports", "knowledge_snapshot")
