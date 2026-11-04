"""Persist script trial identity and comparisons, never private values."""

from alembic import op
import sqlalchemy as sa

revision = "h6c7d8e9f0a1"
down_revision = "g5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_device_script_trials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_bindings.id"),
            nullable=False,
        ),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("expected_digest", sa.String(64), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("grant_digest", sa.String(64), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("reference_json", sa.Text(), nullable=False),
        sa.Column("reference_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("remaining_runs", sa.Integer(), nullable=True),
        sa.Column(
            "dispatch_claimed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "dispatch_deadline_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_trial_request",
        ),
        sa.UniqueConstraint(
            "execution_id", name="uq_ai_script_trial_execution"
        ),
        sa.CheckConstraint(
            "status IN ('unknown','matched','mismatched','failed')",
            name="ck_ai_script_trial_status",
        ),
        sa.CheckConstraint(
            "binding_revision >= 1", name="ck_ai_script_trial_revision"
        ),
        sa.CheckConstraint(
            "remaining_runs IS NULL OR remaining_runs BETWEEN 0 AND 100",
            name="ck_ai_script_trial_remaining",
        ),
        sa.CheckConstraint(
            "length(reference_json) <= 131072",
            name="ck_ai_script_trial_reference",
        ),
        sa.CheckConstraint(
            "length(command_digest) = 64 AND length(expected_digest) = 64 AND "
            "length(reference_digest) = 64 AND length(policy_digest) = 64 AND "
            "length(grant_digest) = 64 AND "
            "(result_digest IS NULL OR length(result_digest) = 64)",
            name="ck_ai_script_trial_digests",
        ),
    )
    for name in ("project_id", "agent_id", "binding_id"):
        op.create_index(
            f"ix_ai_agent_device_script_trials_{name}",
            "ai_agent_device_script_trials",
            [name],
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        conn.execute(
            sa.text(
                "LOCK TABLE ai_agent_device_script_trials "
                "IN ACCESS EXCLUSIVE MODE"
            )
        )
    if conn.execute(
        sa.text("SELECT count(*) FROM ai_agent_device_script_trials")
    ).scalar():
        raise RuntimeError(
            "Script trial history exists; use a compatible rollback"
        )
    op.drop_table("ai_agent_device_script_trials")
