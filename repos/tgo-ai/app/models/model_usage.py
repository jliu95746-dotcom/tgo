"""Private model-call accounting, separate from the API's customer credit ledger."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ModelUsageRecord(BaseModel):
    __tablename__ = "ai_model_usage_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','succeeded','failed','cancelled')",
            name="ck_model_usage_status",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_model_usage_input"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_model_usage_output"
        ),
        CheckConstraint(
            "estimated_cost_fen IS NULL OR estimated_cost_fen >= 0",
            name="ck_model_usage_cost",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    reservation_id: Mapped[UUID | None] = mapped_column(index=True)
    model_name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    input_rate_fen: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    output_rate_fen: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_cost_fen: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
