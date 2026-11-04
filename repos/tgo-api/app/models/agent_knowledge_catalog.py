"""Immutable question versions owned by the API."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentKnowledgeCatalog(Base):
    __tablename__ = "api_agent_knowledge_catalogs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "agent_id",
            "revision",
            name="uq_agent_knowledge_revision",
        ),
        UniqueConstraint(
            "project_id",
            "configured_by",
            "request_key",
            name="uq_agent_knowledge_request",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    configured_by: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff.id"), nullable=False
    )
    request_key: Mapped[UUID] = mapped_column(nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    questions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
