"""Add customer logistics settings, archive, and tracking events.

Revision ID: 0030_customer_logistics
Revises: 0029_multimodal_model_config
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_customer_logistics"
down_revision: Union[str, None] = "0029_multimodal_model_config"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "api_logistics_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_capture_visitor_messages", sa.Boolean(), nullable=False),
        sa.Column("auto_capture_staff_messages", sa.Boolean(), nullable=False),
        sa.Column("verify_before_binding", sa.Boolean(), nullable=False),
        sa.Column("auto_query_on_mention", sa.Boolean(), nullable=False),
        sa.Column("query_tool_id", sa.UUID(), nullable=True),
        sa.Column("poll_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("stop_after_delivered", sa.Boolean(), nullable=False),
        sa.Column("archive_after_days", sa.Integer(), nullable=False),
        sa.Column("conflict_policy", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "archive_after_days BETWEEN 1 AND 3650",
            name="ck_logistics_settings_archive_days",
        ),
        sa.CheckConstraint(
            "conflict_policy IN ('manual_review', 'keep_first')",
            name="ck_logistics_settings_conflict_policy",
        ),
        sa.CheckConstraint(
            "poll_interval_minutes BETWEEN 5 AND 10080",
            name="ck_logistics_settings_poll_interval",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_table(
        "api_customer_shipments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("visitor_id", sa.UUID(), nullable=False),
        sa.Column("tracking_no_ciphertext", sa.Text(), nullable=False),
        sa.Column("tracking_no_hash", sa.String(length=64), nullable=False),
        sa.Column("tracking_no_masked", sa.String(length=64), nullable=False),
        sa.Column("carrier_code", sa.String(length=64), nullable=True),
        sa.Column("carrier_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("verification_state", sa.String(length=32), nullable=False),
        sa.Column("latest_summary", sa.Text(), nullable=True),
        sa.Column("last_source_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('visitor_message', 'staff_message', 'manual', 'order_sync')",
            name="ck_customer_shipment_source",
        ),
        sa.CheckConstraint(
            "status IN ('unknown', 'pending', 'active', 'in_transit', 'delivered', 'exception')",
            name="ck_customer_shipment_status",
        ),
        sa.CheckConstraint(
            "verification_state IN ('pending', 'verified', 'conflict')",
            name="ck_customer_shipment_verification",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["visitor_id"], ["api_visitors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "tracking_no_hash",
            name="uq_customer_shipment_project_tracking_hash",
        ),
    )
    op.create_index(
        "ix_customer_shipment_project_status",
        "api_customer_shipments",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_customer_shipment_project_visitor_updated",
        "api_customer_shipments",
        ["project_id", "visitor_id", "updated_at"],
    )
    op.create_table(
        "api_shipment_tracking_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shipment_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["api_customer_shipments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shipment_id",
            "event_time",
            "description",
            name="uq_shipment_tracking_event",
        ),
    )
    op.create_index(
        "ix_shipment_tracking_event_shipment_time",
        "api_shipment_tracking_events",
        ["shipment_id", "event_time"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shipment_tracking_event_shipment_time",
        table_name="api_shipment_tracking_events",
    )
    op.drop_table("api_shipment_tracking_events")
    op.drop_index(
        "ix_customer_shipment_project_visitor_updated",
        table_name="api_customer_shipments",
    )
    op.drop_index(
        "ix_customer_shipment_project_status",
        table_name="api_customer_shipments",
    )
    op.drop_table("api_customer_shipments")
    op.drop_table("api_logistics_settings")
