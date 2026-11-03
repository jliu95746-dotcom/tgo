"""Append-only publication records owned by the workflow service."""

from datetime import datetime
from uuid import uuid4

from pydantic import JsonValue
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

from app.database import Base


class WorkflowRelease(Base):
    __tablename__ = "wf_workflow_releases"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_release_version"
        ),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflows.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON, nullable=False
    )
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
