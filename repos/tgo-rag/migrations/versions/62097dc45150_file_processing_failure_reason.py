"""Persist file processing failures instead of transient ORM attributes."""

from alembic import op
import sqlalchemy as sa

revision = "62097dc45150"
down_revision = "52097dc45149"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "rag_files", sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column("rag_files", "error_message")
