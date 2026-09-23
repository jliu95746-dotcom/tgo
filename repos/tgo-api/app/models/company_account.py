"""Opt-in company subscription state and durable email actions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CompanyAccount(Base):
    __tablename__ = "api_company_accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','trial','active','expired','suspended')",
            name="ck_company_account_status",
        ),
        CheckConstraint("seat_limit >= 0", name="ck_company_seat_limit"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    trial_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seat_limit: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("api_billing_plans.id"))
    billing_months: Mapped[int | None] = mapped_column(Integer)
    anchor_day: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class EmailAction(Base):
    __tablename__ = "api_email_actions"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify','reset','invite')",
            name="ck_email_action_purpose",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"))
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("api_staff.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class EmailOutbox(Base):
    __tablename__ = "api_email_outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"))
    action_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_email_actions.id"),
        unique=True,
    )
    encrypted_payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(100))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AICreditBatch(Base):
    __tablename__ = "api_ai_credit_batches"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_credit_amount"),
        CheckConstraint("remaining >= 0", name="ck_credit_remaining"),
        CheckConstraint("remaining <= amount", name="ck_credit_balance"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(160), unique=True)
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("api_billing_orders.id"))
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
