"""Durable IM notification intent for a committed manual session command."""

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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionCommandDelivery(Base):
    __tablename__ = "api_session_command_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "client_msg_no",
            name="uq_session_command_delivery_message",
        ),
        CheckConstraint(
            "kind IN ('close','transfer')",
            name="ck_session_command_delivery_kind",
        ),
        CheckConstraint(
            "status IN ('pending','sending','sent','unknown','skipped')",
            name="ck_session_command_delivery_status",
        ),
        CheckConstraint(
            "effects_status IN ('pending','done','unknown','skipped')",
            name="ck_session_command_effects_status",
        ),
        Index("ix_session_command_delivery_recovery", "status", "updated_at"),
        Index(
            "ix_session_command_effects_recovery",
            "effects_status",
            "effects_updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitor_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff.id", ondelete="CASCADE"), nullable=False
    )
    from_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    to_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    session_version: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    client_msg_no: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[int] = mapped_column(Integer, nullable=False)
    from_uid: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(12), default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(80))
    message_id: Mapped[str | None] = mapped_column(String(100))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effects: Mapped[list[dict[str, str]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=list,
        nullable=False,
    )
    effects_status: Mapped[str] = mapped_column(
        String(12), default="done", nullable=False
    )
    effects_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    effects_last_error: Mapped[str | None] = mapped_column(String(80))
    effects_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
