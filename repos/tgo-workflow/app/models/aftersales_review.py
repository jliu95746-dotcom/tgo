"""A staff review task created only after a matching workflow approval."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class WorkflowAfterSalesReview(Base):
    __tablename__ = "wf_aftersales_reviews"
    __table_args__ = (
        UniqueConstraint(
            "approval_id", name="uq_wf_aftersales_review_approval"
        ),
        Index(
            "ix_wf_aftersales_review_project_status", "project_id", "status"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    execution_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("wf_workflow_executions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("wf_approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    visitor_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending_staff_review",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    review_result: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    intake_order_reference: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    intake_sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    intake_request_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    intake_policy_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    intake_evidence_file_ids: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )
    intake_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    intake_prepared_by: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    intake_prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
