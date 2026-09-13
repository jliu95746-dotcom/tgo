"""Durable outbound intent and independently recoverable chat history."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StaffMessageDelivery(Base):
    __tablename__ = "api_staff_message_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "staff_id", "client_msg_no", name="uq_staff_delivery_identity"
        ),
        CheckConstraint(
            "external_status IN ('not_required','sending','sent','failed','unknown')",
            name="ck_staff_delivery_external",
        ),
        CheckConstraint(
            "history_status IN ('pending','sent')", name="ck_staff_delivery_history"
        ),
        CheckConstraint(
            "training_status IN ('none','pending','saved','unavailable')",
            name="ck_staff_delivery_training",
        ),
        Index(
            "ix_staff_delivery_training_recovery",
            "training_status",
            "training_next_retry_at",
        ),
        Index(
            "ix_staff_delivery_recovery",
            "history_status",
            "external_status",
            "next_retry_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE")
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff.id", ondelete="CASCADE")
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE")
    )
    channel_id: Mapped[str] = mapped_column(String(255))
    channel_type: Mapped[int] = mapped_column(Integer)
    client_msg_no: Mapped[str] = mapped_column(String(100))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    external_status: Mapped[str] = mapped_column(String(20))
    history_status: Mapped[str] = mapped_column(String(20), default="pending")
    training_snapshot: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    training_status: Mapped[str] = mapped_column(String(20), default="none")
    training_error: Mapped[str | None] = mapped_column(String(100))
    training_attempts: Mapped[int] = mapped_column(Integer, default=0)
    training_next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    history_error: Mapped[str | None] = mapped_column(String(100))
    history_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
