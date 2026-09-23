"""One-use codes issued by platform operators for manual trial activation."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrialActivationCode(Base):
    __tablename__ = "api_trial_activation_codes"
    __table_args__ = (
        CheckConstraint(
            "(redeemed_at IS NULL) = (redeemed_project_id IS NULL)",
            name="ck_trial_code_redemption_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_platform_operators.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_projects.id")
    )
