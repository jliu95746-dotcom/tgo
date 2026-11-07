"""AI-owned link from an API setup draft to its single saved employee."""
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentDraftBinding(BaseModel):
    __tablename__ = "ai_agent_draft_bindings"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "draft_id", name="uq_ai_agent_setup_draft"
        ),
        UniqueConstraint("agent_id", name="uq_ai_agent_setup_target"),
    )
    project_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
