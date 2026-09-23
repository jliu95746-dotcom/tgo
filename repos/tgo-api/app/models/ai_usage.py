"""Durable reply reservations and immutable quota movement records."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIUsageReservation(Base):
    __tablename__ = "api_ai_usage_reservations"
    __table_args__ = (
        UniqueConstraint("project_id", "round_key", name="uq_ai_usage_round"),
        CheckConstraint(
            "status IN ('reserved','publishing','settled','released','review')",
            name="ck_ai_usage_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    round_key: Mapped[str] = mapped_column(String(160))
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("api_ai_credit_batches.id"))
    status: Mapped[str] = mapped_column(String(24), default="reserved")
    lease_id: Mapped[UUID] = mapped_column(default=uuid4)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    receipt: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIUsageMovement(Base):
    __tablename__ = "api_ai_usage_movements"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    reservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_ai_usage_reservations.id")
    )
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("api_ai_credit_batches.id"))
    business_key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
