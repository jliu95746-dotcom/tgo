"""Immutable record of a legacy employee workflow release transition."""

from uuid import UUID

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentWorkflowPinTransition(BaseModel):
    __tablename__ = "ai_agent_workflow_pin_transitions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_workflow_pin_request",
        ),
        UniqueConstraint(
            "binding_id", name="uq_ai_workflow_legacy_pin_binding"
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    requested_by: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(index=True)
    binding_id: Mapped[UUID] = mapped_column()
    workflow_id: Mapped[str] = mapped_column(String(36))
    old_release_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    old_release_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    old_release_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    release_version: Mapped[int] = mapped_column(Integer)
    release_id: Mapped[str] = mapped_column(String(36))
    release_digest: Mapped[str] = mapped_column(String(64))
    agent_active: Mapped[bool] = mapped_column(Boolean)
    binding_enabled: Mapped[bool] = mapped_column(Boolean)
