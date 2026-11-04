"""An administrator's durable decision on a retained result."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptReview(BaseModel):
    __tablename__ = "ai_agent_device_script_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_ai_script_review_decision",
        ),
        CheckConstraint(
            "expires_at > created_at AND (revoked_at IS NULL OR "
            "(decision = 'approved' AND revoked_at >= created_at))",
            name="ck_ai_script_review_timeline",
        ),
        CheckConstraint(
            "length(case_digest) = 64 AND length(result_digest) = 64",
            name="ck_ai_script_review_digests",
        ),
    )

    project_id: Mapped[UUID] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("ai_agents.id"))
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_trials.id"), unique=True
    )
    case_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_cases.id")
    )
    case_digest: Mapped[str] = mapped_column(String(64))
    result_digest: Mapped[str] = mapped_column(String(64))
    decision: Mapped[Literal["approved", "rejected"]] = mapped_column(
        String(8)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
