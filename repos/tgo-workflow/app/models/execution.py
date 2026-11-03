from sqlalchemy import String, JSON, Integer, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from datetime import datetime
from typing import Optional, List
from pydantic import JsonValue
from app.database import Base
import uuid

class WorkflowExecution(Base):
    __tablename__ = "wf_workflow_executions"
    __table_args__ = (
        Index("ix_wf_execution_business_request", "project_id", "business_request_id"),
        Index("ix_wf_execution_parent", "project_id", "parent_execution_id"),
        Index("ix_wf_initial_dispatch_recovery", "status", "execution_mode",
              "last_initial_dispatch_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String, ForeignKey("wf_workflows.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String, default="pending")
    input: Mapped[Optional[dict]] = mapped_column(JSON)
    invocation_input_hmac: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    event_receipt_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    definition_snapshot: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    definition_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    release_id: Mapped[str | None] = mapped_column(String, nullable=True)
    employee_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ai_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    visitor_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_lineage: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    active_approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_segment_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_initial_dispatch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    initial_dispatch_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    output: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration: Mapped[Optional[int]] = mapped_column(Integer)  # in milliseconds

    # Relationships
    node_executions: Mapped[List["NodeExecution"]] = relationship(back_populates="workflow_execution")

class NodeExecution(Base):
    __tablename__ = "wf_node_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String, ForeignKey("wf_workflow_executions.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    node_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    input: Mapped[Optional[dict]] = mapped_column(JSON)
    output: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration: Mapped[Optional[int]] = mapped_column(Integer)

    # Relationships
    workflow_execution: Mapped["WorkflowExecution"] = relationship(back_populates="node_executions")

