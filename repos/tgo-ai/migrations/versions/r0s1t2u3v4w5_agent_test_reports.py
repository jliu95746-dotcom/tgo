"""Persist private employee trial reports without changing active employees."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r0s1t2u3v4w5"
down_revision = "q9r0s1t2u3v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.UUID(as_uuid=True)
    op.create_table(
        "ai_agent_test_reports",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("project_id", uuid_type, nullable=False),
        sa.Column("created_by", uuid_type, nullable=False),
        sa.Column("agent_id", uuid_type, nullable=False),
        sa.Column("request_key", uuid_type, nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64)),
        sa.Column("knowledge_status", sa.String(20)),
        sa.Column("sources", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "created_by", "request_key", name="uq_ai_agent_trial_request"),
    )
    for field in ("project_id", "created_by", "agent_id"):
        op.create_index(f"ix_ai_agent_test_reports_{field}", "ai_agent_test_reports", [field])


def downgrade() -> None:
    op.drop_table("ai_agent_test_reports")
