"""Append-only human judgments bind to immutable trial evidence."""
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as DatabaseUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentTrialReview(BaseModel):
    __tablename__ = "ai_agent_trial_reviews"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "reviewed_by",
            "request_key",
            name="uq_ai_trial_review_request",
        ),
        UniqueConstraint(
            "project_id",
            "reviewed_by",
            "report_id",
            "case_id",
            "revision",
            name="uq_ai_trial_review_revision",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(
        DatabaseUUID(as_uuid=True), index=True
    )
    reviewed_by: Mapped[UUID] = mapped_column(
        DatabaseUUID(as_uuid=True), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        DatabaseUUID(as_uuid=True), index=True
    )
    report_id: Mapped[UUID] = mapped_column(
        DatabaseUUID(as_uuid=True), index=True
    )
    request_key: Mapped[UUID] = mapped_column(DatabaseUUID(as_uuid=True))
    request_digest: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    case_digest: Mapped[str] = mapped_column(String(64))
    report_digest: Mapped[str] = mapped_column(String(64))
    case_spec: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    knowledge_snapshot: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    decision: Mapped[str] = mapped_column(String(20))
    checks: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    note: Mapped[str] = mapped_column(Text, default="")
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
