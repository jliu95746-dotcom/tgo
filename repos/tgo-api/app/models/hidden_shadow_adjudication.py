"""Immutable third-person resolution of an independent G4 dispute."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HiddenShadowAdjudication(Base):
    __tablename__ = "api_hidden_shadow_adjudications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["protocol_id", "candidate_id"],
            [
                "api_hidden_shadow_protocol_cases.protocol_id",
                "api_hidden_shadow_protocol_cases.candidate_id",
            ],
            ondelete="RESTRICT",
            name="fk_hidden_adjudication_frozen_case",
        ),
        UniqueConstraint(
            "protocol_id", "candidate_id",
            name="uq_hidden_adjudication_case",
        ),
        UniqueConstraint(
            "project_id", "request_id",
            name="uq_hidden_adjudication_request",
        ),
        CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', 'severe_error')",
            name="ck_hidden_adjudication_verdict",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('wrong_fact', 'privacy', 'unsafe_action', "
            "'missing_handoff', 'other')",
            name="ck_hidden_adjudication_reason",
        ),
        CheckConstraint(
            "length(trim(note)) > 0", name="ck_hidden_adjudication_note",
        ),
        Index("ix_hidden_adjudication_protocol", "protocol_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    protocol_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False)
    adjudicator_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    review_set_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
