"""Employee-owned setup progress; never stores credentials or enabled state."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScenarioDraft(Base):
    __tablename__ = "api_scenario_drafts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "created_by",
            "idempotency_key",
            name="uq_scenario_draft_request",
        ),
        CheckConstraint("revision >= 1", name="ck_scenario_draft_revision"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("api_staff.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str] = mapped_column(String(50), nullable=False)
    module: Mapped[str] = mapped_column(String(30), nullable=False)
    template_version: Mapped[int] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    step: Mapped[str] = mapped_column(
        String(30), default="business", nullable=False
    )
    business: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    bindings: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    agent_settings: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
