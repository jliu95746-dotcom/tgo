"""Append-only operation judgments and immutable original request receipts."""

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


class AgentBusinessReview(BaseModel):
    __tablename__ = "ai_agent_business_reviews"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "reviewed_by",
            "request_id",
            name="uq_ai_business_review_request",
        ),
        UniqueConstraint(
            "project_id",
            "report_id",
            "revision",
            name="uq_ai_business_review_revision",
        ),
        CheckConstraint(
            "decision IN ('passed', 'rejected')",
            name="ck_ai_business_review_decision",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    reviewed_by: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(index=True)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_business_trials.id"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    request_digest: Mapped[str] = mapped_column(String(64))
    report_digest: Mapped[str] = mapped_column(String(64))
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    result_json: Mapped[str] = mapped_column(Text)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
