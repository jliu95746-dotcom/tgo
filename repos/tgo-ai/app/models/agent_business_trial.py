"""AI-owned business reports retain digests and safe receipts only."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import BaseModel


class AgentBusinessTrial(BaseModel):
    __tablename__ = "ai_agent_business_trials"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "actor_id", "request_id", name="uq_ai_business_trial_request"
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name="ck_ai_business_trial_status",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("ai_agents.id"), index=True)
    connection_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    attempt_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(String(32))
    binding_id: Mapped[UUID] = mapped_column()
    binding_revision: Mapped[int] = mapped_column(Integer)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    expected_revision: Mapped[int] = mapped_column(Integer)
    configuration_revision: Mapped[int] = mapped_column(Integer)
    authorization_revision: Mapped[int] = mapped_column(Integer)
    resource_revision: Mapped[str] = mapped_column(String(64))
    configuration_test_id: Mapped[UUID] = mapped_column()
    configuration_test_valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    command_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    query_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
