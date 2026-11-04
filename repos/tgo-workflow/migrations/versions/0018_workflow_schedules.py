"""Persist timer configuration and original command snapshots.

Revision ID: 0018_workflow_schedules
Revises: 0017_event_receipt_keys
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_workflow_schedules"
down_revision = "0017_event_receipt_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wf_workflow_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(),
            sa.ForeignKey("wf_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("cron_expression", sa.String(512), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("updated_by", sa.String(36), nullable=False),
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
        sa.UniqueConstraint(
            "project_id", "workflow_id", name="uq_wf_schedule_owner"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_wf_schedule_revision"),
        sa.CheckConstraint(
            "(enabled AND next_run_at IS NOT NULL) OR "
            "(NOT enabled AND next_run_at IS NULL)",
            name="ck_wf_schedule_due",
        ),
    )
    op.create_index(
        "ix_wf_workflow_schedules_project_id",
        "wf_workflow_schedules",
        ["project_id"],
    )
    op.create_index(
        "ix_wf_schedule_due",
        "wf_workflow_schedules",
        ["enabled", "next_run_at"],
    )
    op.create_table(
        "wf_workflow_schedule_commands",
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(),
            sa.ForeignKey("wf_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_staff_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "operation IN ('save', 'disable')",
            name="ck_wf_schedule_command_operation",
        ),
    )
    op.create_index(
        "ix_wf_workflow_schedule_commands_workflow_id",
        "wf_workflow_schedule_commands",
        ["workflow_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE wf_workflow_schedules, "
                "wf_workflow_schedule_commands IN ACCESS EXCLUSIVE MODE"
            )
        )
    for table in ("wf_workflow_schedule_commands", "wf_workflow_schedules"):
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("已有定时计划或请求回执，不能删除；请保留兼容代码回退。")
    op.drop_table("wf_workflow_schedule_commands")
    op.drop_table("wf_workflow_schedules")
