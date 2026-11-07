"""Default model templates per provider."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIProviderDefaultModel(Base):
    """Database-backed default model templates used when creating providers."""

    __tablename__ = "api_ai_provider_default_models"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model_id",
            name="uq_ai_provider_default_models_provider_model_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Provider key (e.g. openai, dashscope, openai_compatible)",
    )
    model_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Model identifier",
    )
    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name shown in UI when seeded",
    )
    model_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="chat",
        comment="Model type: chat or embedding",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Ordering used when choosing first default model",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether this default template is enabled",
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug repr
        return (
            f'AIProviderDefaultModel(provider='
            f'{self.provider!r}'
            f', model_id='
            f'{self.model_id!r}'
            f', model_type='
            f'{self.model_type!r}'
            f')'
        )
