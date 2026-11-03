"""Original request preparation and cancellation survive delayed creation."""

from alembic import op
import sqlalchemy as sa

revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade():
    columns = [sa.Column("id", sa.Uuid(), primary_key=True)]
    columns += [
        sa.Column(name, sa.Uuid(), nullable=False)
        for name in ("project_id", "actor_id", "connection_id", "request_id")
    ]
    columns += [
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id"), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("command_digest", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(32)),
        sa.Column("report_id", sa.Uuid()),
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
        "ai_agent_business_requests",
        *columns,
        sa.UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_business_preparation_request",
        ),
        sa.CheckConstraint(
            "status IN ('preparing', 'ready', 'failed', 'cancelled')",
            name="ck_ai_business_preparation_status",
        ),
    )
    for name in ("project_id", "agent_id"):
        op.create_index(
            "ix_ai_agent_business_requests_" + name,
            "ai_agent_business_requests",
            [name],
        )
    # Existing report IDs and original digests retain their former request scope.
    op.execute(
        sa.text("""
        INSERT INTO ai_agent_business_requests
            (id, project_id, actor_id, agent_id, connection_id, request_id, operation,
             command_digest, status, deadline_at, completed_at, report_id, created_at, updated_at)
        SELECT id, project_id, actor_id, agent_id, connection_id, request_id, operation,
               command_digest, 'ready', expires_at, created_at, id, created_at, updated_at
        FROM ai_agent_business_trials
    """)
    )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE ai_agent_business_requests IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text("SELECT count(*) FROM ai_agent_business_requests")
    ).scalar():
        raise RuntimeError(
            "Preparation or cancellation receipts exist; use a compatible rollback"
        )
    op.drop_table("ai_agent_business_requests")
