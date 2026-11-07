"""AI-owned explicit role and channel requirements, independent of activation."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentActivationPolicy(BaseModel):
    __tablename__ = "ai_agent_activation_policies"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "agent_id",
            "channel",
            name="uq_ai_agent_policy_channel",
        ),
        CheckConstraint(
            "revision >= 1 AND template_version >= 1",
            name="ck_ai_agent_policy_versions",
        ),
        CheckConstraint(
            "(source_draft_id IS NULL AND source_draft_revision IS NULL) OR "
            "(source_draft_id IS NOT NULL AND source_draft_revision IS NOT NULL AND source_draft_revision >= 1)",
            name="ck_ai_agent_policy_source",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), nullable=False, index=True
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    updated_by: Mapped[UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    template_id: Mapped[str] = mapped_column(String(50), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    requirements_revision: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_draft_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_draft_revision: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
