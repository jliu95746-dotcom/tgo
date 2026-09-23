"""Commercial ledger: prices, payments and fulfillment are separate records."""

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


class BillingPlan(Base):
    __tablename__ = "api_billing_plans"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_billing_plan_version"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    definition: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BillingQuote(Base):
    __tablename__ = "api_billing_quotes"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("api_staff.id"))
    base_version: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BillingOrder(Base):
    __tablename__ = "api_billing_orders"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_order_amount"),
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name="ck_order_refunded",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    number: Mapped[str] = mapped_column(
        String(32), unique=True, default=lambda: uuid4().hex
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    quote_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_billing_quotes.id"), unique=True
    )
    amount: Mapped[int] = mapped_column(Integer)
    refunded_amount: Mapped[int] = mapped_column(Integer, default=0)
    payment_status: Mapped[str] = mapped_column(String(24), default="pending")
    fulfillment_status: Mapped[str] = mapped_column(String(24), default="pending")
    transaction_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    code_url: Mapped[str | None] = mapped_column(String(1024))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SubscriptionPeriod(Base):
    __tablename__ = "api_subscription_periods"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_billing_orders.id"), unique=True
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("api_billing_plans.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    months: Mapped[int] = mapped_column(Integer)
    anchor_day: Mapped[int] = mapped_column(Integer)
    original_price: Mapped[int] = mapped_column(Integer)
    current_price: Mapped[int] = mapped_column(Integer)
    original_definition: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    definition: Mapped[dict[str, JsonValue]] = mapped_column(JSON)


class SeatAddon(Base):
    __tablename__ = "api_seat_addons"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_billing_orders.id"), unique=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentEvent(Base):
    __tablename__ = "api_payment_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(String(160), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("api_billing_orders.id"))
    transaction_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BillingJob(Base):
    __tablename__ = "api_billing_jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_key: Mapped[str] = mapped_column(String(160), unique=True)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_projects.id"), index=True
    )
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("api_billing_orders.id"))
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(100))


class BillingRefund(Base):
    __tablename__ = "api_billing_refunds"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    number: Mapped[str] = mapped_column(
        String(32), unique=True, default=lambda: uuid4().hex
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("api_billing_orders.id"))
    operator_id: Mapped[UUID] = mapped_column(ForeignKey("api_platform_operators.id"))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="requested")
    reason: Mapped[str] = mapped_column(String(500))
    disposition: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    provider_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BillingAudit(Base):
    __tablename__ = "api_billing_audits"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operator_id: Mapped[UUID] = mapped_column(ForeignKey("api_platform_operators.id"))
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_projects.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(String(500))
    detail: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InvoiceRequest(Base):
    __tablename__ = "api_invoice_requests"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_billing_orders.id"), unique=True
    )
    title: Mapped[str] = mapped_column(String(200))
    tax_number: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(24), default="requested")
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
