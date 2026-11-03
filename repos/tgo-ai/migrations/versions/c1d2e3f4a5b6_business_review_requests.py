"""Persist exact review intentions and original-key cancellation receipts."""

from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    columns = [sa.Column("id", sa.Uuid(), primary_key=True)]
    columns += [
        sa.Column(name, sa.Uuid(), nullable=False)
        for name in ("project_id", "actor_id", "report_id", "request_id")
    ]
    columns += [
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id"), nullable=False),
        sa.Column("command_digest", sa.String(64)),
        sa.Column("command_json", sa.Text()),
        sa.Column("result_json", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(32)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]
    columns += [
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        )
        for name in ("created_at", "updated_at")
    ]
    op.create_table(
        "ai_agent_business_review_requests",
        *columns,
        sa.UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_business_review_intention",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'cancelled')",
            name="ck_ai_business_review_request_status",
        ),
    )
    for name in ("project_id", "agent_id"):
        op.create_index(
            "ix_ai_agent_business_review_requests_" + name,
            "ai_agent_business_review_requests",
            [name],
        )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE ai_agent_business_review_requests IN ACCESS EXCLUSIVE MODE"
            )
        )
    if connection.execute(
        sa.text("SELECT count(*) FROM ai_agent_business_review_requests")
    ).scalar():
        raise RuntimeError(
            "Review intentions or cancellation receipts exist; use a compatible rollback"
        )
    op.drop_table("ai_agent_business_review_requests")
