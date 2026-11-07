"""Project model."""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.ai_provider import AIProvider
    from app.models.platform import Platform
    from app.models.project_ai_config import ProjectAIConfig
    from app.models.project_onboarding import ProjectOnboardingProgress
    from app.models.staff import Staff
    from app.models.tag import Tag
    from app.models.visitor import Visitor
    from app.models.visitor_assignment_rule import VisitorAssignmentRule
    from app.models.visitor_tag import VisitorTag


class Project(Base):
    """Project model for multi-tenant isolation."""

    __tablename__ = "api_projects"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Basic fields
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Project name"
    )
    api_key: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        comment="API key for authentication",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation timestamp"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Soft deletion timestamp"
    )

    # Relationships
    platforms: Mapped[List["Platform"]] = relationship(
        "Platform",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    staff: Mapped[List["Staff"]] = relationship(
        "Staff",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    visitors: Mapped[List["Visitor"]] = relationship(
        "Visitor",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    tags: Mapped[List["Tag"]] = relationship(
        "Tag",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    visitor_tags: Mapped[List["VisitorTag"]] = relationship(
        "VisitorTag",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )
    ai_providers: Mapped[List["AIProvider"]] = relationship(
        "AIProvider",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    ai_config: Mapped[Optional["ProjectAIConfig"]] = relationship(
        "ProjectAIConfig",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="select",
    )

    onboarding_progress: Mapped[
        Optional["ProjectOnboardingProgress"]
    ] = relationship(
        "ProjectOnboardingProgress",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="select",
    )

    visitor_assignment_rule: Mapped[
        Optional["VisitorAssignmentRule"]
    ] = relationship(
        "VisitorAssignmentRule",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        """String representation of the project."""
        return f"<Project(id={self.id}, name='{self.name}')>"

    @property
    def is_deleted(self) -> bool:
        """Check if the project is soft deleted."""
        return self.deleted_at is not None
