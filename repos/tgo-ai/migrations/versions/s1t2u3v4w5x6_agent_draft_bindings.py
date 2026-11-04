"""Durable setup links; preserve all existing employees and activation states."""
from alembic import op
import sqlalchemy as sa

revision = "s1t2u3v4w5x6"
down_revision = "r0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.UUID(as_uuid=True)
    op.create_table(
        "ai_agent_draft_bindings",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("project_id", uuid_type, nullable=False),
        sa.Column("created_by", uuid_type, nullable=False),
        sa.Column("draft_id", uuid_type, nullable=False),
        sa.Column("agent_id", uuid_type, sa.ForeignKey("ai_agents.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "draft_id", name="uq_ai_agent_setup_draft"),
        sa.UniqueConstraint("agent_id", name="uq_ai_agent_setup_target"),
    )


def downgrade() -> None:
    # Removes only setup receipts; saved inactive employees remain recoverable.
    op.drop_table("ai_agent_draft_bindings")
