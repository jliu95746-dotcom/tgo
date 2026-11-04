"""Persist immutable employee knowledge question catalogs."""
from alembic import op
import sqlalchemy as sa

revision = "0081_agent_knowledge_catalogs"
down_revision = "0080_merge_customer_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_agent_knowledge_catalogs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "configured_by",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id"),
            nullable=False,
        ),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "agent_id",
            "revision",
            name="uq_agent_knowledge_revision",
        ),
        sa.UniqueConstraint(
            "project_id",
            "configured_by",
            "request_key",
            name="uq_agent_knowledge_request",
        ),
    )


def downgrade() -> None:
    op.drop_table("api_agent_knowledge_catalogs")
