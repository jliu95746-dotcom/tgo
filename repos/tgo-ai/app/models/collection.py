"""Collection models for knowledge base management."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.project import Project


class Collection(BaseModel):
    """Project-level knowledge collection metadata."""

    __tablename__ = "ai_collections"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Associated project ID",
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Collection display name",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional collection description",
    )

    external_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="External RAG service collection identifier",
    )

    project: Mapped["Project"] = relationship(
        "Project",
        primaryjoin="foreign(Collection.project_id) == Project.id",
        back_populates="collections",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f'<Collection(id='
            f'{self.id}'
            f', project_id='
            f'{self.project_id}'
            f", name='"
            f'{self.name}'
            f"')>"
        )


class AgentCollection(BaseModel):
    """
    Many-to-many relationship between agents and collections.

    Represents which collections an agent has access to for
    knowledge retrieval operations. Collection data is retrieved
    from external RAG service using the stored collection_id.
    """

    __tablename__ = "ai_agent_collections"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_agents.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated agent ID",
    )

    collection_id: Mapped[str] = mapped_column(
        String(36),  # UUID string length
        nullable=False,
        comment=(
            'Collection ID (UUID string) - references external RAG service'
        ),
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether this collection is enabled for the agent",
    )

    # Relationships
    agent: Mapped["Agent"] = relationship(
        "Agent",
        back_populates="collections",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """String representation of the agent collection relationship."""
        return (
            f'<AgentCollection(agent_id='
            f'{self.agent_id}'
            f", collection_id='"
            f'{self.collection_id}'
            f"', enabled="
            f'{self.enabled}'
            f')>'
        )
