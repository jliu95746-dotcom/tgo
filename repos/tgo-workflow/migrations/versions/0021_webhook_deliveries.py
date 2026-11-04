"""Keep confidential, durable webhook notifications and retry fences.

Revision ID: 0021_webhook_deliveries
Revises: 0020_workflow_webhooks
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_webhook_deliveries"
down_revision = "0020_workflow_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wf_webhook_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "hook_id",
            sa.String(36),
            sa.ForeignKey("wf_workflow_webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(),
            sa.ForeignKey("wf_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_staff_id", sa.String(36), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("payload_key_id", sa.String(32), nullable=False),
        sa.Column("payload_receipt", sa.String(64), nullable=False),
        sa.Column("payload_ciphertext", sa.Text()),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_id", sa.String(36), unique=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "hook_id", "event_key", name="uq_wf_webhook_event"
        ),
        sa.CheckConstraint(
            "binding_revision >= 1 AND credential_revision >= 1 "
            "AND attempts >= 0",
            name="ck_wf_webhook_delivery_numbers",
        ),
        sa.CheckConstraint(
            "(status IN ('pending','queued') "
            "AND payload_ciphertext IS NOT NULL) OR "
            "(status IN ('completed','failed','cancelled',"
            "'expired','blocked') "
            "AND payload_ciphertext IS NULL)",
            name="ck_wf_webhook_payload_state",
        ),
    )
    op.create_index(
        "ix_wf_webhook_deliveries_project_id",
        "wf_webhook_deliveries",
        ["project_id"],
    )
    op.create_index(
        "ix_wf_webhook_delivery_due",
        "wf_webhook_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE wf_webhook_deliveries IN ACCESS EXCLUSIVE MODE"
            )
        )
    if connection.scalar(
        sa.text("SELECT count(*) FROM wf_webhook_deliveries")
    ):
        raise RuntimeError("已有外部通知来源，不能删除；请保留兼容代码回退。")
    op.drop_table("wf_webhook_deliveries")
