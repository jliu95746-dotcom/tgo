"""Append private knowledge case reviews without activating employees."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_trial_reviews",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        *[sa.Column(name, sa.UUID(as_uuid=True), nullable=False)
          for name in ["project_id", "reviewed_by", "agent_id", "report_id", "request_key"]],
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("case_digest", sa.String(64), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("case_spec", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("knowledge_snapshot", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("checks", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "reviewed_by", "request_key", name="uq_ai_trial_review_request"),
        sa.UniqueConstraint("project_id", "reviewed_by", "report_id", "case_id", "revision", name="uq_ai_trial_review_revision"),
    )
    for column in ["project_id", "reviewed_by", "agent_id", "report_id"]:
        op.create_index(f"ix_ai_agent_trial_reviews_{column}", "ai_agent_trial_reviews", [column])


def downgrade() -> None:
    # Review history is lost if dropped; trial reports and employees remain.
    op.drop_table("ai_agent_trial_reviews")
