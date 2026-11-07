"""Durable preparation receipt; private command inputs are never persisted."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import BaseModel


class AgentBusinessRequest(BaseModel):
    __tablename__ = "ai_agent_business_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_business_preparation_request",
        ),
        CheckConstraint(
            "status IN ('preparing', 'ready', 'failed', 'cancelled')",
            name="ck_ai_business_preparation_status",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(String(32))
    command_digest: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(32))
    report_id: Mapped[UUID | None] = mapped_column()
