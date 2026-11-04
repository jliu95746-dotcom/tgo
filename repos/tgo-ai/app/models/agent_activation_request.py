"""Terminal activation receipts commit atomically with employee switching."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentActivationRequestRecord(BaseModel):
    __tablename__ = "ai_agent_activation_requests"
    __table_args__ = (
        UniqueConstraint("project_id", "requested_by", "request_key", name="uq_ai_activation_request_key"),
        CheckConstraint(
            "(status='pending' AND finished_at IS NULL AND failure_code IS NULL) OR "
            "(status='completed' AND finished_at IS NOT NULL AND failure_code IS NULL) OR "
            "(status='rejected' AND finished_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_ai_activation_request_outcome",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("ai_agents.id"), nullable=False, index=True)
    request_key: Mapped[UUID] = mapped_column(nullable=False)
    command_json: Mapped[str] = mapped_column(Text, nullable=False)
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
