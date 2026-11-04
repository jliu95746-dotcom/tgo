"""Pin a verified device read policy to each AI employee binding.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

from alembic import op
import sqlalchemy as sa

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_agents",
        sa.Column("bound_device_policy_digest", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE ai_agents IN ACCESS EXCLUSIVE MODE")
    has_pins = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ai_agents "
            "WHERE bound_device_policy_digest IS NOT NULL)"
        )
    ).scalar()
    if has_pins:
        raise RuntimeError("Refusing to remove pinned device policies")
    op.drop_column("ai_agents", "bound_device_policy_digest")
