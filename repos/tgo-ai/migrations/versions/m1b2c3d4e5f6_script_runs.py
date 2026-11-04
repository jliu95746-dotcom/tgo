"""Retain business dispatch identities without private input bodies."""

from alembic import op
import sqlalchemy as sa

revision = "m1b2c3d4e5f6"
down_revision = "l0a1b2c3d4e5"
branch_labels = None
depends_on = None
TABLE = "ai_agent_device_script_runs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_trials.id"),
            nullable=False,
        ),
        sa.Column(
            "review_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_reviews.id"),
            nullable=False,
        ),
        sa.Column("command_digest", sa.String(64), nullable=True),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("reference_json", sa.Text(), nullable=True),
        sa.Column("reference_digest", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column(
            "deadline_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True)),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_run_request",
        ),
        sa.UniqueConstraint(
            "execution_id", name="uq_ai_script_run_execution"
        ),
        sa.CheckConstraint(
            "status IN ('preparing','unknown','succeeded',"
            "'failed','cancelled')",
            name="ck_ai_script_run_status",
        ),
        sa.CheckConstraint(
            "deadline_at > created_at AND "
            "((status IN ('succeeded','failed','cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('preparing','unknown') AND completed_at IS NULL))",
            name="ck_ai_script_run_timeline",
        ),
        sa.CheckConstraint(
            "(reference_json IS NULL AND reference_digest IS NULL "
            "AND execution_id IS NULL) OR (reference_json IS NOT NULL "
            "AND reference_digest IS NOT NULL "
            "AND length(reference_json) <= 131072 "
            "AND length(reference_digest) = 64 AND execution_id IS NOT NULL)",
            name="ck_ai_script_run_reference",
        ),
        sa.CheckConstraint(
            "status NOT IN ('unknown','succeeded') OR "
            "(execution_id IS NOT NULL AND dispatch_started_at IS NOT NULL)",
            name="ck_ai_script_run_dispatch",
        ),
        sa.CheckConstraint(
            "(command_digest IS NULL OR length(command_digest) = 64) AND "
            "(result_digest IS NULL OR length(result_digest) = 64)",
            name="ck_ai_script_run_digests",
        ),
    )
    for column in ("project_id", "agent_id"):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    )
    if connection.execute(
        sa.text(f"SELECT count(*) FROM {TABLE}")
    ).scalar():
        raise RuntimeError(
            "Business run history exists; preserve receipts"
        )
    op.drop_table(TABLE)
