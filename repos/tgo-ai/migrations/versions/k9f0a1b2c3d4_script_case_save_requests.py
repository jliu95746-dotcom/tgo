"""Persist save preparation and cancellation before any device validation."""

from alembic import op
import sqlalchemy as sa

revision = "k9f0a1b2c3d4"
down_revision = "j8e9f0a1b2c3"
branch_labels = None
depends_on = None
TABLE = "ai_agent_device_script_case_saves"


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
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_cases.id"),
            nullable=True,
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
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
            "actor_id",
            "request_id",
            name="uq_ai_script_case_save_request",
        ),
        sa.CheckConstraint(
            "status IN ('preparing','saved','failed','cancelled')",
            name="ck_ai_script_case_save_status",
        ),
        sa.CheckConstraint(
            "binding_revision >= 1 AND expected_revision >= 0",
            name="ck_ai_script_case_save_revision",
        ),
        sa.CheckConstraint(
            "(case_id IS NULL AND expected_revision = 0) OR "
            "(case_id IS NOT NULL AND expected_revision > 0)",
            name="ck_ai_script_case_save_root",
        ),
        sa.CheckConstraint(
            "command_digest IS NULL OR length(command_digest)=64",
            name="ck_ai_script_case_save_digest",
        ),
        sa.CheckConstraint(
            "(status='preparing' AND version_id IS NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status='saved' AND version_id IS NOT NULL AND "
            "completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status='failed' AND version_id IS NULL AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL AND "
            "error_code IN "
            "('preparation_failed','preparation_expired')) OR "
            "(status='cancelled' AND version_id IS NULL AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL AND "
            "error_code='cancelled')",
            name="ck_ai_script_case_save_outcome",
        ),
    )
    for column in ("project_id", "agent_id"):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar():
        raise RuntimeError(
            "Script save history exists; preserve request fences"
        )
    op.drop_table(TABLE)
