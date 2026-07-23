"""Customer logistics archive models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LogisticsSettings(Base):
    """Project-level rules for customer logistics archives."""

    __tablename__ = "api_logistics_settings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_capture_visitor_messages: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    auto_capture_staff_messages: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    verify_before_binding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    auto_query_on_mention: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    query_tool_id: Mapped[UUID | None] = mapped_column(nullable=True)
    poll_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=360
    )
    stop_after_delivered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    archive_after_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    conflict_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual_review"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "poll_interval_minutes BETWEEN 5 AND 10080",
            name="ck_logistics_settings_poll_interval",
        ),
        CheckConstraint(
            "archive_after_days BETWEEN 1 AND 3650",
            name="ck_logistics_settings_archive_days",
        ),
        CheckConstraint(
            "conflict_policy IN ('manual_review', 'keep_first')",
            name="ck_logistics_settings_conflict_policy",
        ),
    )


class CustomerShipment(Base):
    """One tracking number associated with a customer profile."""

    __tablename__ = "api_customer_shipments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"), nullable=False
    )
    tracking_no_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    tracking_no_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_no_masked: Mapped[str] = mapped_column(String(64), nullable=False)
    carrier_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    latest_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_source_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "tracking_no_hash",
            name="uq_customer_shipment_project_tracking_hash",
        ),
        CheckConstraint(
            "status IN "
            "('unknown', 'pending', 'active', 'in_transit', 'delivered', 'exception')",
            name="ck_customer_shipment_status",
        ),
        CheckConstraint(
            "source IN ('visitor_message', 'staff_message', 'manual', 'order_sync')",
            name="ck_customer_shipment_source",
        ),
        CheckConstraint(
            "verification_state IN ('pending', 'verified', 'conflict')",
            name="ck_customer_shipment_verification",
        ),
        Index(
            "ix_customer_shipment_project_visitor_updated",
            "project_id",
            "visitor_id",
            "updated_at",
        ),
        Index(
            "ix_customer_shipment_project_status",
            "project_id",
            "status",
        ),
    )


class ShipmentTrackingEvent(Base):
    """Normalized tracking event returned by the configured query tool."""

    __tablename__ = "api_shipment_tracking_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    shipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_customer_shipments.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "shipment_id",
            "event_time",
            "description",
            name="uq_shipment_tracking_event",
        ),
        Index(
            "ix_shipment_tracking_event_shipment_time",
            "shipment_id",
            "event_time",
        ),
    )
