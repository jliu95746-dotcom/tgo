"""Tag model."""

import base64
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.visitor_tag import VisitorTag


class TagCategory(str, Enum):
    """Tag category enumeration."""

    VISITOR = "visitor"
    KNOWLEDGE = "knowledge"


class Tag(Base):
    """Tag model for categorization and labeling system."""

    __tablename__ = "api_tags"

    # Primary key (Base64 encoded ID)
    id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        comment="Base64 encoded ID: base64_encode(name + '@' + category)",
    )

    # Foreign keys
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for multi-tenant isolation",
    )

    # Basic fields
    name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Tag name (English)"
    )
    name_zh: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="Tag name in Chinese"
    )
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            'Tag category: visitor (for customer categorization) or '
            'knowledge (for content categorization)'
        ),
    )
    weight: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=(
            'Tag importance/priority weight (0-10, higher values indicate '
            'higher priority)'
        ),
    )
    color: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="Tag color"
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Tag description"
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
    project: Mapped["Project"] = relationship(
        "Project", back_populates="tags", lazy="select"
    )

    visitor_tags: Mapped[List["VisitorTag"]] = relationship(
        "VisitorTag",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "project_id", "name", name="uk_api_tags_project_name"
        ),
        CheckConstraint(
            category.in_(["visitor", "knowledge"]),
            name="chk_api_tags_category",
        ),
        CheckConstraint(
            "weight >= 0 AND weight <= 10", name="chk_api_tags_weight"
        ),
    )

    def __init__(
        self,
        name: str,
        category: TagCategory,
        project_id: UUID,
        **kwargs: object,
    ) -> None:
        """Initialize tag with auto-generated Base64 ID."""
        # Generate Base64 encoded ID
        id_string = f"{name}@{category.value}"
        encoded_id = base64.b64encode(id_string.encode()).decode()

        super().__init__(
            id=encoded_id,
            name=name,
            category=category,
            project_id=project_id,
            **kwargs,
        )

    def __repr__(self) -> str:
        """String representation of the tag."""
        return (
            f"<Tag(id='"
            f'{self.id}'
            f"', name='"
            f'{self.name}'
            f"', category='"
            f'{self.category}'
            f"')>"
        )

    @property
    def is_deleted(self) -> bool:
        """Check if the tag is soft deleted."""
        return self.deleted_at is not None

    @classmethod
    def generate_id(cls, name: str, category: TagCategory) -> str:
        """Generate Base64 encoded ID for a tag."""
        id_string = f"{name}@{category.value}"
        return base64.b64encode(id_string.encode()).decode()

    @classmethod
    def decode_id(cls, tag_id: str) -> tuple[str, str]:
        """Decode Base64 tag ID to get name and category."""
        try:
            decoded = base64.b64decode(tag_id.encode()).decode()
            name, category = decoded.split("@", 1)
            return name, category
        except Exception:
            raise ValueError(f"Invalid tag ID format: {tag_id}")
