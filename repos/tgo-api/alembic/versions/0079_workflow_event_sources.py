"""Opt-in source bindings and durable workflow event deliveries.

Revision ID: 0079_workflow_event_sources
Revises: 0078_ai_interaction_error_redaction
"""

import sqlalchemy as sa
from alembic import op

revision = "0079_workflow_event_sources"
down_revision = "0078_ai_interaction_error_redaction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_workflow_event_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("release_id", sa.String(128)),
        sa.Column("release_digest", sa.String(64)),
        sa.Column("version", sa.Integer()),
        sa.Column("enabled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "enabled_by_staff_id",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id", ondelete="SET NULL"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "workflow_id", name="uq_workflow_event_subscription"
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_workflow_event_subscription_revision"
        ),
        sa.CheckConstraint(
            "event_type = 'staff.session.closed'",
            name="ck_workflow_event_subscription_source",
        ),
    )
    op.create_table(
        "api_workflow_event_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_workflow_event_subscriptions.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("subscription_revision", sa.Integer(), nullable=False),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column(
            "source_command_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_session_command_deliveries.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("api_visitor_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("encrypted_payload", sa.Text()),
        sa.Column("execution_id", sa.String(128)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subscription_id",
            "source_command_id",
            name="uq_workflow_event_delivery_source",
        ),
        sa.CheckConstraint(
            "status IN ('pending','sending','accepted','failed','cancelled')",
            name="ck_workflow_event_delivery_status",
        ),
        sa.CheckConstraint(
            "subscription_revision >= 1 AND attempts >= 0",
            name="ck_workflow_event_delivery_revision",
        ),
    )
    op.create_index(
        "ix_workflow_event_delivery_due",
        "api_workflow_event_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_workflow_event_delivery_workflow",
        "api_workflow_event_deliveries",
        ["project_id", "workflow_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE api_workflow_event_subscriptions, "
                "api_workflow_event_deliveries IN ACCESS EXCLUSIVE MODE"
            )
        )
    for table in (
        "api_workflow_event_deliveries",
        "api_workflow_event_subscriptions",
    ):
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("工作流事件仍有订阅或投递记录，请保留兼容代码回退")
    op.drop_table("api_workflow_event_deliveries")
    op.drop_table("api_workflow_event_subscriptions")
