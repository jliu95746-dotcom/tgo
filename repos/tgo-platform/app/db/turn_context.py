"""Encrypted delivery routing survives listener restarts without copying keys."""

from datetime import datetime
from uuid import UUID
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models import Base


class CustomerTurnContext(Base):
    __tablename__ = "pt_customer_turn_contexts"
    platform_id: Mapped[UUID] = mapped_column(
        ForeignKey("pt_platforms.id"), primary_key=True
    )
    source_message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    actor_key: Mapped[str] = mapped_column(String(64), index=True)
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    visitor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    has_pending_media: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    media_lease_id: Mapped[UUID | None] = mapped_column(nullable=True)
    media_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_id: Mapped[UUID | None] = mapped_column(nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        Index("ix_pt_turn_registration_queue", "registered_at", "due_at", "sequence"),
    )
