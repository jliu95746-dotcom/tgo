"""Durable human approval and the exact internal continuation cursor."""

import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class WorkflowApprovalRequest(Base):
    __tablename__ = "wf_approval_requests"
    __table_args__ = (
        UniqueConstraint("execution_id", "node_id", name="uq_wf_approval_execution_node"),
        Index(
            "ix_wf_approval_assignee_status",
            "project_id", "approver_staff_id", "status",
        ),
        Index(
            "ix_wf_approval_resume_dispatch",
            "project_id", "status", "last_resume_dispatch_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String, ForeignKey("wf_workflow_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    approver_staff_id: Mapped[str] = mapped_column(String(36), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    checkpoint: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(36))
    last_resume_dispatch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    resume_dispatch_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
