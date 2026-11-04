"""Durable original review intention and a cancellation/termination fence."""

from datetime import datetime
from uuid import UUID
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import BaseModel


class AgentBusinessReviewRequest(BaseModel):
    __tablename__ = "ai_agent_business_review_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_business_review_intention",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'cancelled')",
            name="ck_ai_business_review_request_status",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("ai_agents.id"), index=True)
    report_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    command_digest: Mapped[str | None] = mapped_column(String(64))
    command_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))
