"""Persisted manual-service requests raised by visitors or AI routing."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ManualServiceRequest(Base):
    """Human handoff request stored in the existing manual-service table."""

    __tablename__ = "api_manual_service_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "visitor_id",
            "source_message_id",
            name="uq_manual_service_source_message",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    visitor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="normal",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notification_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    routing_reason: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    request_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
