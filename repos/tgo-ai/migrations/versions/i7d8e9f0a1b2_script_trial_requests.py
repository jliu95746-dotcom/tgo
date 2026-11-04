"""Persist preparation and cancellation fences without business bodies."""

from alembic import op
import sqlalchemy as sa

revision = "i7d8e9f0a1b2"
down_revision = "h6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_device_script_trial_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column(
            "binding_id", sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_bindings.id"),
            nullable=False,
        ),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stop_status", sa.String(16), nullable=False),
        sa.Column(
            "report_id", sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_trials.id"), nullable=True,
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stop_requested_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id", "actor_id", "request_id",
            name="uq_ai_script_trial_preparation_request",
        ),
        sa.UniqueConstraint(
            "report_id", name="uq_ai_script_trial_request_report"
        ),
        sa.CheckConstraint(
            "status IN ('preparing','ready','failed','cancelled')",
            name="ck_ai_script_trial_request_status",
        ),
        sa.CheckConstraint(
            "stop_status IN ('none','requested','confirmed','finished')",
            name="ck_ai_script_trial_request_stop",
        ),
        sa.CheckConstraint(
            "binding_revision >= 1",
            name="ck_ai_script_trial_request_revision",
        ),
        sa.CheckConstraint(
            "command_digest IS NULL OR length(command_digest) = 64",
            name="ck_ai_script_trial_request_digest",
        ),
        sa.CheckConstraint(
            "(status = 'ready' AND report_id IS NOT NULL AND "
            "completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'preparing' AND report_id IS NULL AND "
            "completed_at IS NULL AND error_code IS NULL AND "
            "dispatched_at IS NULL AND stop_status = 'none') OR "
            "(status IN ('failed','cancelled') AND report_id IS NULL AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL AND "
            "dispatched_at IS NULL)",
            name="ck_ai_script_trial_request_outcome",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled' AND error_code = 'cancelled' AND "
            "stop_status = 'confirmed' AND stop_requested_at IS NOT NULL) "
            "OR (status != 'cancelled' AND "
            "(error_code IS NULL OR error_code != 'cancelled'))",
            name="ck_ai_script_trial_request_cancel",
        ),
        sa.CheckConstraint(
            "stop_status != 'requested' OR "
            "(dispatched_at IS NOT NULL AND "
            "stop_requested_at IS NOT NULL)",
            name="ck_ai_script_trial_request_pending_stop",
        ),
    )
    for name in ("project_id", "agent_id", "binding_id"):
        op.create_index(
            f"ix_ai_agent_device_script_trial_requests_{name}",
            "ai_agent_device_script_trial_requests", [name],
        )
    op.execute(sa.text("""
        INSERT INTO ai_agent_device_script_trial_requests
          (id, project_id, actor_id, agent_id, binding_id, binding_revision,
           request_id, command_digest, status, stop_status, report_id,
           deadline_at, completed_at, dispatched_at, created_at, updated_at)
        SELECT id, project_id, actor_id, agent_id, binding_id,
           binding_revision,
           request_id, command_digest, 'ready',
           CASE WHEN status IN ('matched','mismatched') OR error_code IN
             ('execution_failed','result_unavailable')
             THEN 'finished' ELSE 'none' END,
           id, dispatch_deadline_at, created_at, dispatch_claimed_at,
           created_at, updated_at
        FROM ai_agent_device_script_trials
    """))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text(
            "LOCK TABLE ai_agent_device_script_trial_requests "
            "IN ACCESS EXCLUSIVE MODE"
        ))
    if connection.execute(sa.text(
        "SELECT count(*) FROM ai_agent_device_script_trial_requests"
    )).scalar():
        raise RuntimeError(
            "Script trial request history exists; use a compatible rollback"
        )
    op.drop_table("ai_agent_device_script_trial_requests")
