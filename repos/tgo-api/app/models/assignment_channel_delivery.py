"""Durable channel work committed with an accepted staff assignment."""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssignmentChannelDelivery(Base):
    __tablename__ = "api_assignment_channel_deliveries"
    __table_args__ = (
        CheckConstraint(
            "effects_status IN ('pending','unknown','done','skipped')",
            name="ck_assignment_channel_effects_status",
        ),
        CheckConstraint(
            "status IN ('pending','sending','sent','unknown','skipped')",
            name="ck_assignment_channel_notice_status",
        ),
        Index(
            "ix_assignment_channel_effects_recovery",
            "effects_status",
            "effects_updated_at",
        ),
        Index("ix_assignment_channel_notice_recovery", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitor_assignment_history.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitor_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL"),
    )
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[int] = mapped_column(Integer, nullable=False)
    effects: Mapped[list[dict[str, str]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    effects_status: Mapped[str] = mapped_column(
        String(12),
        default="pending",
        nullable=False,
    )
    effects_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    effects_last_error: Mapped[str | None] = mapped_column(String(80))
    effects_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    client_msg_no: Mapped[str] = mapped_column(String(100), nullable=False)
    from_uid: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    actor_authority: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(12),
        default="skipped",
        nullable=False,
    )
    last_error: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
