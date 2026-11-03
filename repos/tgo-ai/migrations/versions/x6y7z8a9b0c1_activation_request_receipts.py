"""Atomic activation request receipts; existing employee state is unchanged."""

from alembic import op
import sqlalchemy as sa

revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_agent_activation_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("command_json", sa.Text(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "request_key",
            name="uq_ai_activation_request_key",
        ),
        sa.CheckConstraint(
            "(status='pending' AND finished_at IS NULL AND failure_code IS NULL) OR "
            "(status='completed' AND finished_at IS NOT NULL AND failure_code IS NULL) OR "
            "(status='rejected' AND finished_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_ai_activation_request_outcome",
        ),
    )
    op.create_index(
        "ix_ai_agent_activation_requests_project_id",
        "ai_agent_activation_requests",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_activation_requests_agent_id",
        "ai_agent_activation_requests",
        ["agent_id"],
    )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE ai_agent_activation_requests IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text("SELECT count(*) FROM ai_agent_activation_requests")
    ).scalar_one():
        raise RuntimeError(
            "Activation receipts exist; preserve them and roll back consumers without dropping history"
        )
    op.drop_table("ai_agent_activation_requests")
