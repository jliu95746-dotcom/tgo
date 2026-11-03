"""Private trial artifacts owned by AI; no customer sessions or raw credentials."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as DatabaseUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentTestReport(BaseModel):
    __tablename__ = "ai_agent_test_reports"
    __table_args__ = (UniqueConstraint(
        "project_id", "created_by", "request_key", name="uq_ai_agent_trial_request",
    ),)

    project_id: Mapped[UUID] = mapped_column(DatabaseUUID(as_uuid=True), index=True)
    created_by: Mapped[UUID] = mapped_column(DatabaseUUID(as_uuid=True), index=True)
    # Logical references preserve reports when an employee is removed.
    agent_id: Mapped[UUID] = mapped_column(DatabaseUUID(as_uuid=True), index=True)
    request_key: Mapped[UUID] = mapped_column(DatabaseUUID(as_uuid=True))
    request_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    configuration_fingerprint: Mapped[str | None] = mapped_column(String(64))
    knowledge_snapshot: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True,
    )
    knowledge_status: Mapped[str | None] = mapped_column(String(20))
    sources: Mapped[list[dict[str, JsonValue]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list,
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
