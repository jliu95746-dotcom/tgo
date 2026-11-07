"""Project onboarding progress tracking model."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.project import Project


class ProjectOnboardingProgress(Base):
    """Tracks onboarding progress for a project (one-to-one with Project).

    Stores completion status for each onboarding step:
    - Step 1: Set up AI Provider
    - Step 2: Set default models
    - Step 3: Create RAG Collection
    - Step 4: Create Agent with knowledge base
    - Step 5: Start first chat
    """

    __tablename__ = "api_project_onboarding_progress"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # One-to-one with Project
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID (unique)",
    )

    # Step completion flags
    step_1_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether AI provider setup is completed",
    )
    step_2_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether default models are configured",
    )
    step_3_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether RAG collection is created",
    )
    step_4_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether agent with knowledge base is created",
    )
    step_5_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether first chat is started",
    )

    # Overall completion status
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether all onboarding steps are completed or skipped",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="Timestamp when onboarding was completed or skipped",
    )

    # Timestamps (soft delete)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project", back_populates="onboarding_progress", lazy="select"
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", name="uq_project_onboarding_project_id"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug
        return (
            f'<ProjectOnboardingProgress(project_id='
            f'{self.project_id}'
            f', is_completed='
            f'{self.is_completed}'
            f')>'
        )
