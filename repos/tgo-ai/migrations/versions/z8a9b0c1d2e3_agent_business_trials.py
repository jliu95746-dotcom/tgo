"""Durable, purpose-limited employee business tests without private inputs."""

from alembic import op
import sqlalchemy as sa

revision = "z8a9b0c1d2e3"
down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    columns = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id"), nullable=False),
    ]
    columns += [
        sa.Column(name, sa.Uuid(), nullable=False)
        for name in (
            "connection_id",
            "request_id",
            "attempt_id",
            "binding_id",
            "configuration_test_id",
        )
    ]
    columns += [
        sa.Column(name, sa.Integer(), nullable=False)
        for name in (
            "binding_revision",
            "expected_revision",
            "configuration_revision",
            "authorization_revision",
        )
    ]
    columns += [
        sa.Column(name, sa.String(64), nullable=False)
        for name in ("configuration_fingerprint", "resource_revision", "command_digest")
    ]
    columns += [
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("receipt_json", sa.Text()),
    ]
    columns += [
        sa.Column(name, sa.DateTime(timezone=True), nullable=False)
        for name in ("expires_at", "configuration_test_valid_until")
    ]
    columns += [
        sa.Column(name, sa.DateTime(timezone=True), nullable=True)
        for name in ("dispatched_at", "query_claimed_at", "completed_at", "deleted_at")
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
        "ai_agent_business_trials",
        *columns,
        sa.UniqueConstraint(
            "project_id", "actor_id", "request_id", name="uq_ai_business_trial_request"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name="ck_ai_business_trial_status",
        ),
    )
    for name in ("project_id", "agent_id"):
        op.create_index(
            "ix_ai_agent_business_trials_" + name, "ai_agent_business_trials", [name]
        )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE ai_agent_business_trials IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text("SELECT count(*) FROM ai_agent_business_trials")
    ).scalar():
        raise RuntimeError("Business trial reports exist; use a compatible rollback")
    op.drop_table("ai_agent_business_trials")
