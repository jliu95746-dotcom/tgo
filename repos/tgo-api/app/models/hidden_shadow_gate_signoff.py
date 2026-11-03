"""Human G4 signoff bound to independent frozen shadow evidence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HiddenShadowGateSignoff(Base):
    __tablename__ = "api_hidden_shadow_gate_signoffs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "protocol_id", name="uq_hidden_gate_protocol",
        ),
        UniqueConstraint(
            "project_id", "request_id", name="uq_hidden_gate_request",
        ),
        Index("ix_hidden_gate_project_created", "project_id", "created_at"),
        CheckConstraint("sample_count > 0", name="ck_hidden_gate_sample"),
        CheckConstraint(
            "live_case_count = sample_count", name="ck_hidden_gate_live_cases",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('accepted', 'rejected')",
            name="ck_hidden_gate_decision",
        ),
        CheckConstraint(
            "(decision IS NULL AND decider_id IS NULL "
            "AND decision_request_id IS NULL AND decision_fingerprint IS NULL "
            "AND decision_note IS NULL AND decided_at IS NULL) OR "
            "(decision IS NOT NULL AND decider_id IS NOT NULL "
            "AND decision_request_id IS NOT NULL "
            "AND decision_fingerprint IS NOT NULL "
            "AND decision_note IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_hidden_gate_decision_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    protocol_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_hidden_shadow_protocols.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requester_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_evidence_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_reference: Mapped[str] = mapped_column(
        String(120), nullable=False,
    )
    live_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    decision: Mapped[str | None] = mapped_column(String(16))
    decider_id: Mapped[UUID | None] = mapped_column()
    decision_request_id: Mapped[UUID | None] = mapped_column()
    decision_fingerprint: Mapped[str | None] = mapped_column(String(64))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    @property
    def status(self) -> str:
        return self.decision or "pending"
