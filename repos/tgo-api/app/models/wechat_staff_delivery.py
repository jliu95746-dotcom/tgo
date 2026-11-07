"""Immutable origin and claim for a human reply returning to its WeChat bridge."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.staff_message_delivery import utc_now


class WeChatStaffDelivery(Base):
    __tablename__ = "api_wechat_staff_deliveries"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued','sending','sent','unknown','cancelled')",
            name="ck_wechat_staff_state",
        ),
        Index(
            "ix_wechat_staff_pending",
            "platform_id",
            "visitor_id",
            "state",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff_message_deliveries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"))
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("api_platforms.id"))
    visitor_id: Mapped[UUID] = mapped_column(ForeignKey("api_visitors.id"))
    platform_open_id: Mapped[str] = mapped_column(String(255))
    owner_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitor_sessions.id")
    )
    owner_staff_id: Mapped[UUID] = mapped_column(ForeignKey("api_staff.id"))
    owner_revision: Mapped[UUID | None] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    attempt_id: Mapped[UUID | None] = mapped_column()
    claim_id: Mapped[UUID | None] = mapped_column()
    receipt: Mapped[str | None] = mapped_column(String(512))
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
