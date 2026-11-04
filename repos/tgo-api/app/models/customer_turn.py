"""Durable customer turns and their original, independently deduplicated parts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CustomerTurn(Base):
    __tablename__ = "api_customer_turns"
    __table_args__ = (
        Index("ix_customer_turn_actor", "actor_key", "created_at"),
        Index("ix_customer_turn_publish", "state", "im_published_at", "due_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"))
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("api_platforms.id"))
    visitor_id: Mapped[UUID] = mapped_column(ForeignKey("api_visitors.id"))
    channel_id: Mapped[str] = mapped_column(String(255))
    channel_type: Mapped[int] = mapped_column(Integer, default=251)
    actor_key: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(24), default="collecting", index=True)
    can_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    external_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    client_msg_no: Mapped[str] = mapped_column(String(100))
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_id: Mapped[UUID | None] = mapped_column(nullable=True)
    receipt: Mapped[str | None] = mapped_column(String(512), nullable=True)
    permit: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    failure: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lease_owner: Mapped[UUID | None] = mapped_column(nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collect_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    im_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomerTurnPart(Base):
    __tablename__ = "api_customer_turn_parts"
    __table_args__ = (
        Index(
            "uq_customer_turn_source", "platform_id", "source_message_id", unique=True
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_customer_turns.id"), index=True
    )
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("api_platforms.id"))
    source_message_id: Mapped[str] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    media_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    forwarded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
