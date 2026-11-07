"""
Base model class and common database utilities.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional, TypeVar

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


ModelType = TypeVar("ModelType", bound=Base)


class TimestampMixin:
    """Mixin for models with timestamp fields."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        nullable=False,
        doc="Record creation timestamp",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
        doc="Record last update timestamp",
    )


class SoftDeleteMixin:
    """Mixin for models with soft delete functionality."""

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Record deletion timestamp (NULL if not deleted)",
    )

    @property
    def is_deleted(self) -> bool:
        """Check if the record is soft deleted."""
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Mark the record as deleted."""
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        """Restore a soft deleted record."""
        self.deleted_at = None


class UUIDMixin:
    """Mixin for models with UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unique identifier",
    )


def to_dict(obj: Base, exclude: Optional[set[str]] = None) -> Dict[str, Any]:
    """
    Convert SQLAlchemy model instance to dictionary.

    Args:
        obj: SQLAlchemy model instance
        exclude: Set of field names to exclude from the result

    Returns:
        Dictionary representation of the model
    """
    exclude = exclude or set()
    result = {}

    for column in obj.__table__.columns:
        if column.name not in exclude:
            value = getattr(obj, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, uuid.UUID):
                value = str(value)
            result[column.name] = value

    return result


def from_dict(model_class: type[ModelType], data: Dict[str, Any]) -> ModelType:
    """
    Create SQLAlchemy model instance from dictionary.

    Args:
        model_class: SQLAlchemy model class
        data: Dictionary with model data

    Returns:
        Model instance
    """
    # Filter out keys that don't exist as columns
    valid_columns = {column.name for column in model_class.__table__.columns}
    filtered_data = {k: v for k, v in data.items() if k in valid_columns}

    return model_class(**filtered_data)
