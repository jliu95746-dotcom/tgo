"""Daily statement evidence without retaining payer identities or download tokens."""

from datetime import date, datetime

from pydantic import JsonValue
from sqlalchemy import JSON, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BillingReconciliation(Base):
    __tablename__ = "api_billing_reconciliations"
    bill_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    source_hash: Mapped[str] = mapped_column(String(64))
    entry_count: Mapped[int] = mapped_column(Integer)
    issue_count: Mapped[int] = mapped_column(Integer)
    issues: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSON)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
