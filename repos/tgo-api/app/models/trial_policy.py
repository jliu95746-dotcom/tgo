"""Immutable trial policy versions; existing credit grants remain snapshots."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrialPolicy(Base):
    __tablename__ = "api_trial_policies"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_trial_policy_version"),
        CheckConstraint(
            "ai_replies >= 0 AND ai_replies <= 100000", name="ck_trial_policy_replies"
        ),
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ai_replies: Mapped[int] = mapped_column(Integer)
    operator_id: Mapped[UUID] = mapped_column(ForeignKey("api_platform_operators.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
