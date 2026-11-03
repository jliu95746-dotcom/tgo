"""Persist credential-free business setup drafts with optimistic revisions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0047_scenario_drafts"
down_revision = "0046_operator_authorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_scenario_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("api_staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("template_id", sa.String(50), nullable=False),
        sa.Column("module", sa.String(30), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(30), nullable=False),
        sa.Column("business", postgresql.JSONB(), nullable=False),
        sa.Column("bindings", postgresql.JSONB(), nullable=False),
        sa.Column("agent_settings", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "created_by", "idempotency_key", name="uq_scenario_draft_request"),
        sa.CheckConstraint("revision >= 1", name="ck_scenario_draft_revision"),
    )
    op.create_index("ix_api_scenario_drafts_project_id", "api_scenario_drafts", ["project_id"])
    op.create_index("ix_api_scenario_drafts_created_by", "api_scenario_drafts", ["created_by"])


def downgrade() -> None:
    op.drop_table("api_scenario_drafts")
