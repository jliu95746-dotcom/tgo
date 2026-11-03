"""Opt-in workflow source subscriptions and durable event deliveries."""

from datetime import datetime, timezone
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowEventSubscription(Base):
    __tablename__ = "api_workflow_event_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "workflow_id", name="uq_workflow_event_subscription"
        ),
        CheckConstraint(
            "revision >= 1", name="ck_workflow_event_subscription_revision"
        ),
        CheckConstraint(
            "event_type = 'staff.session.closed'",
            name="ck_workflow_event_subscription_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(100), default="staff.session.closed", nullable=False
    )
    release_id: Mapped[str | None] = mapped_column(String(128))
    release_digest: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int | None] = mapped_column(Integer)
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    enabled_by_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class WorkflowEventDelivery(Base):
    __tablename__ = "api_workflow_event_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "source_command_id",
            name="uq_workflow_event_delivery_source",
        ),
        CheckConstraint(
            "status IN ('pending','sending','accepted','failed','cancelled')",
            name="ck_workflow_event_delivery_status",
        ),
        CheckConstraint(
            "subscription_revision >= 1 AND attempts >= 0",
            name="ck_workflow_event_delivery_revision",
        ),
        Index("ix_workflow_event_delivery_due", "status", "next_attempt_at"),
        Index(
            "ix_workflow_event_delivery_workflow",
            "project_id",
            "workflow_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_workflow_event_subscriptions.id", ondelete="CASCADE")
    )
    subscription_revision: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[str] = mapped_column(String(128))
    source_command_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_session_command_deliveries.id", ondelete="CASCADE")
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitor_sessions.id", ondelete="CASCADE")
    )
    release_id: Mapped[str] = mapped_column(String(128))
    release_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(12), default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    execution_id: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
