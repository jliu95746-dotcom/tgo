"""Current fixed approval and immutable original selection write receipts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
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


class AgentDeviceScriptSelection(BaseModel):
    __tablename__ = "ai_agent_device_script_selections"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "agent_id", name="uq_ai_script_selection_agent"
        ),
        CheckConstraint(
            "revision >= 1 AND revision <= 2147483647 AND "
            "binding_revision >= 1 AND binding_revision <= 2147483647",
            name="ck_ai_script_selection_revision",
        ),
        CheckConstraint(
            "length(selection_digest) = 64",
            name="ck_ai_script_selection_digest",
        ),
        CheckConstraint(
            "NOT enabled OR expires_at > saved_at",
            name="ck_ai_script_selection_time",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("ai_agents.id"))
    request_id: Mapped[UUID] = mapped_column()
    configured_by: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_bindings.id"),
    )
    binding_revision: Mapped[int] = mapped_column(Integer)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_trials.id"),
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agent_device_script_reviews.id"),
    )
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    selection_digest: Mapped[str] = mapped_column(String(64))


class AgentDeviceScriptSelectionRequest(BaseModel):
    __tablename__ = "ai_agent_device_script_selection_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_script_selection_request",
        ),
        CheckConstraint(
            "length(command_digest) = 64 AND length(result_digest) = 64 "
            "AND length(result_json) <= 8192",
            name="ck_ai_script_selection_receipt",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("ai_agents.id"))
    requested_by: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    command_digest: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
    result_digest: Mapped[str] = mapped_column(String(64))
