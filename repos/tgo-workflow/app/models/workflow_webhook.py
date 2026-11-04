"""Webhook bindings and recoverable command facts, owned by Workflow."""

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowWebhook(Base):
    __tablename__ = "wf_workflow_webhooks"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "workflow_id", name="uq_wf_webhook_owner"
        ),
        CheckConstraint(
            "revision >= 1 AND credential_revision >= 1",
            name="ck_wf_webhook_revisions",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    release_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_staff_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WorkflowWebhookCommand(Base):
    __tablename__ = "wf_workflow_webhook_commands"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('save','disable','rotate')",
            name="ck_wf_webhook_command_operation",
        ),
    )

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("wf_workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_staff_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_issued: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_snapshot: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
