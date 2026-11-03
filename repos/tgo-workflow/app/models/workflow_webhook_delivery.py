"""Durable, confidential external notifications and their execution links."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowWebhookDelivery(Base):
    __tablename__ = "wf_webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("hook_id", "event_key", name="uq_wf_webhook_event"),
        CheckConstraint(
            "binding_revision >= 1 AND credential_revision >= 1 "
            "AND attempts >= 0",
            name="ck_wf_webhook_delivery_numbers",
        ),
        CheckConstraint(
            "(status IN ('pending','queued') "
            "AND payload_ciphertext IS NOT NULL) OR "
            "(status IN ('completed','failed','cancelled',"
            "'expired','blocked') "
            "AND payload_ciphertext IS NULL)",
            name="ck_wf_webhook_payload_state",
        ),
        Index("ix_wf_webhook_delivery_due", "status", "next_attempt_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    hook_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflow_webhooks.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_staff_id: Mapped[str] = mapped_column(String(36), nullable=False)
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_receipt: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_ciphertext: Mapped[str | None] = mapped_column(Text)
    release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    release_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    execution_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
