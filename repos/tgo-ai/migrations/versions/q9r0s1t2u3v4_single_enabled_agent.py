"""Allow at most one enabled employee per account without changing settings."""
from alembic import op
import sqlalchemy as sa

revision = "q9r0s1t2u3v4"
down_revision = "p8q9r0s1t2u3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_ai_agents_enabled_per_project",
        "ai_agents",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true AND deleted_at IS NULL"),
        sqlite_where=sa.text("is_active = 1 AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_agents_enabled_per_project", table_name="ai_agents")
