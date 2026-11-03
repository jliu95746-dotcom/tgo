"""Persist webhook bindings and encrypted credentials without discarding audit.

Revision ID: 0020_workflow_webhooks
Revises: 0019_timer_occurrences
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_workflow_webhooks"
down_revision = "0019_timer_occurrences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wf_workflow_webhooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(),
            sa.ForeignKey("wf_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("credential_ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("actor_staff_id", sa.String(36), nullable=False),
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
            "project_id", "workflow_id", name="uq_wf_webhook_owner"
        ),
        sa.CheckConstraint(
            "revision >= 1 AND credential_revision >= 1",
            name="ck_wf_webhook_revisions",
        ),
    )
    op.create_index(
        "ix_wf_workflow_webhooks_project_id",
        "wf_workflow_webhooks",
        ["project_id"],
    )
    op.create_table(
        "wf_workflow_webhook_commands",
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
        sa.Column("credential_issued", sa.Boolean(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "operation IN ('save','disable','rotate')",
            name="ck_wf_webhook_command_operation",
        ),
    )
    op.create_index(
        "ix_wf_workflow_webhook_commands_workflow_id",
        "wf_workflow_webhook_commands",
        ["workflow_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE wf_workflow_webhooks, "
                "wf_workflow_webhook_commands "
                "IN ACCESS EXCLUSIVE MODE"
            )
        )
    for table in ("wf_workflow_webhooks", "wf_workflow_webhook_commands"):
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("已有外部触发配置或请求回执，不能删除；请保留兼容代码回退。")
    op.drop_table("wf_workflow_webhook_commands")
    op.drop_table("wf_workflow_webhooks")
