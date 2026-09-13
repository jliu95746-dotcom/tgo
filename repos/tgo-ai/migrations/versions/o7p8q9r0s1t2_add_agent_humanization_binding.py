"""Optional employee style binding; no existing skills or visitors are changed."""
from alembic import op
import sqlalchemy as sa

revision = "o7p8q9r0s1t2"
down_revision = "n6o7p8q9r0s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_agents", sa.Column("humanization_skill_name", sa.String(64), nullable=True))
    op.add_column("ai_agents", sa.Column("humanization_skill_enabled", sa.Boolean(),
                                      nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("ai_agents", "humanization_skill_enabled")
    op.drop_column("ai_agents", "humanization_skill_name")
