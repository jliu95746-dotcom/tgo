"""Durable dispatch identity and status, without business bodies."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeviceScriptExecution(Base):
    __tablename__ = "dc_script_executions"
    __table_args__ = (
        UniqueConstraint("step_id", name="uq_dc_script_execution_step"),
        Index("ix_dc_script_execution_grant", "device_id", "grant_digest"),
        CheckConstraint(
            "policy_revision BETWEEN 1 AND 2147483647",
            name="ck_dc_script_execution_revision",
        ),
        CheckConstraint(
            "status IN ('unknown','succeeded','failed','canceled','timed_out',"
            "'output_exceeded','rejected','start_failed')",
            name="ck_dc_script_execution_status",
        ),
        CheckConstraint(
            "(status = 'succeeded') = (result_digest IS NOT NULL)",
            name="ck_dc_script_execution_result",
        ),
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dc_devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dc_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dc_session_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    workflow_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    script_id: Mapped[str] = mapped_column(String(48), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    grant_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    result_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
