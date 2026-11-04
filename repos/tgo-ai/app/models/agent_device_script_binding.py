"""Configuration-only script pins and durable original command receipts."""

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDeviceScriptBinding(BaseModel):
    __tablename__ = "ai_agent_device_script_bindings"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "agent_id", name="uq_ai_agent_script_binding"
        ),
        CheckConstraint("revision >= 1", name="ck_ai_agent_script_revision"),
        CheckConstraint(
            "length(policy_digest) = 64 AND length(grant_digest) = 64",
            name="ck_ai_agent_script_digests",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    device_id: Mapped[UUID] = mapped_column()
    script_id: Mapped[str] = mapped_column(String(48))
    policy_digest: Mapped[str] = mapped_column(String(64))
    grant_digest: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer)
    configured_by: Mapped[UUID] = mapped_column()


class AgentDeviceScriptBindingRequest(BaseModel):
    __tablename__ = "ai_agent_device_script_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_agent_script_request",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    requested_by: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    command_digest: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
