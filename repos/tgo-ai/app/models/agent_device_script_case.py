"""Immutable business-case versions with separately expiring encrypted body."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptCase(BaseModel):
    __tablename__ = "ai_agent_device_script_cases"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "agent_id",
            "case_id",
            "revision",
            name="uq_ai_script_case_version",
        ),
        UniqueConstraint(
            "project_id",
            "created_by",
            "request_id",
            name="uq_ai_script_case_write",
        ),
        CheckConstraint(
            "revision >= 1 AND binding_revision >= 1",
            name="ck_ai_script_case_revision",
        ),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 80", name="ck_ai_script_case_title"
        ),
        CheckConstraint(
            "length(command_digest) = 64 AND length(case_digest) = 64 AND "
            "length(policy_digest) = 64 AND length(grant_digest) = 64",
            name="ck_ai_script_case_digests",
        ),
        CheckConstraint(
            "(ciphertext IS NOT NULL AND cleared_at IS NULL AND "
            "length(ciphertext) <= 200000) OR "
            "(ciphertext IS NULL AND cleared_at IS NOT NULL)",
            name="ck_ai_script_case_body",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    created_by: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    case_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(80))
    binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_bindings.id")
    )
    binding_revision: Mapped[int] = mapped_column(Integer)
    device_id: Mapped[UUID] = mapped_column()
    policy_digest: Mapped[str] = mapped_column(String(64))
    grant_digest: Mapped[str] = mapped_column(String(64))
    command_digest: Mapped[str] = mapped_column(String(64))
    case_digest: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
