"""Persist timer source identity, retry fencing and operational suspension.

Revision ID: 0019_timer_occurrences
Revises: 0018_workflow_schedules
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_timer_occurrences"
down_revision = "0018_workflow_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wf_workflow_schedules", sa.Column("suspension_reason", sa.String(64))
    )
    op.add_column(
        "wf_workflow_schedules",
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "wf_timer_occurrences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column(
            "schedule_id",
            sa.String(36),
            sa.ForeignKey("wf_workflow_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_staff_id", sa.String(36), nullable=False),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("last_failure_code", sa.String(64)),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("execution_id", sa.String(36), unique=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "schedule_id", "revision", "scheduled_at", name="uq_wf_timer_due"
        ),
        sa.CheckConstraint(
            "status IN ('pending','queued','skipped','cancelled','blocked')",
            name="ck_wf_timer_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND revision >= 1", name="ck_wf_timer_attempts"
        ),
    )
    op.create_index(
        "ix_wf_timer_occurrences_project_id",
        "wf_timer_occurrences",
        ["project_id"],
    )
    op.create_index(
        "ix_wf_timer_occurrences_status", "wf_timer_occurrences", ["status"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE wf_timer_occurrences, "
                "wf_workflow_schedules IN ACCESS EXCLUSIVE MODE"
            )
        )
    if connection.scalar(
        sa.text("SELECT count(*) FROM wf_timer_occurrences")
    ) or connection.scalar(
        sa.text(
            "SELECT count(*) FROM wf_workflow_schedules "
            "WHERE suspension_reason IS NOT NULL OR suspended_at IS NOT NULL"
        )
    ):
        raise RuntimeError("已有定时来源或暂停记录，不能删除；请保留兼容代码回退。")
    op.drop_table("wf_timer_occurrences")
    op.drop_column("wf_workflow_schedules", "suspended_at")
    op.drop_column("wf_workflow_schedules", "suspension_reason")
