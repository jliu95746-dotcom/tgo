"""Store verified, hidden shadow candidates separately from staff drafts.

Revision ID: 0068_hidden_shadow_runs
Revises: 0067_ai_execution_business_queries
"""

from alembic import op
import sqlalchemy as sa

revision = "0068_hidden_shadow_runs"
down_revision = "0067_ai_execution_business_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_hidden_shadow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("platform_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.String(255), nullable=False),
        sa.Column("source_message_seq", sa.Integer(), nullable=False),
        sa.Column("source_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_content_sha256", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("generation_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("knowledge_status", sa.String(16), nullable=True),
        sa.Column("knowledge_evidence_sha256", sa.String(64), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("configured_prompt_sha256", sa.String(64), nullable=True),
        sa.Column("ai_execution_id", sa.Uuid(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answer_sha256", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["platform_id"], ["api_platforms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["visitor_id"], ["api_visitors.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "platform_id", "source_message_id",
                            name="uq_hidden_shadow_source"),
        sa.CheckConstraint("status IN ('running','ready','blocked','failed')",
                           name="ck_hidden_shadow_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_hidden_shadow_attempts"),
    )
    op.create_index("ix_hidden_shadow_project_created", "api_hidden_shadow_runs",
                    ["project_id", "created_at"])
    op.create_index("ix_hidden_shadow_project_visitor", "api_hidden_shadow_runs",
                    ["project_id", "visitor_id"])


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM api_hidden_shadow_runs LIMIT 1")
    ).first() is not None:
        raise RuntimeError("Hidden shadow runs exist; archive them before downgrade")
    op.drop_index("ix_hidden_shadow_project_visitor",
                  table_name="api_hidden_shadow_runs")
    op.drop_index("ix_hidden_shadow_project_created",
                  table_name="api_hidden_shadow_runs")
    op.drop_table("api_hidden_shadow_runs")
