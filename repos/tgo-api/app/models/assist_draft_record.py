"""Persist server-issued assist drafts for human comparison."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssistDraftRecord(Base):
    __tablename__ = "api_assist_draft_records"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "staff_id",
            "request_id",
            name="uq_assist_draft_staff_request",
        ),
        Index(
            "ix_assist_draft_project_visitor_source",
            "project_id",
            "visitor_id",
            "source_message_id",
        ),
        Index(
            "ix_assist_draft_project_created",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    response_data: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    source_proof: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
