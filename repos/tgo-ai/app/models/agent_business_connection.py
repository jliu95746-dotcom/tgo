"AI owns explicit connection selections and durable configuration \
receipts."
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AgentBusinessConnection(BaseModel):
    __tablename__ = "ai_agent_business_connections"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "agent_id",
            "connection_id",
            name="uq_ai_employee_connection",
        ),
        CheckConstraint(
            (
                'revision >= 1 AND configuration_revision >= 1 AND '
                'authorization_revision >= 1'
            ),
            name="ck_ai_employee_connection_revisions",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column()
    operations: Mapped[list[str]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer)
    configuration_revision: Mapped[int] = mapped_column(Integer)
    authorization_revision: Mapped[int] = mapped_column(Integer)
    resource_revision: Mapped[str] = mapped_column(String(64))
    configured_by: Mapped[UUID] = mapped_column()


class AgentBusinessConnectionRequest(BaseModel):
    __tablename__ = "ai_agent_business_connection_requests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_employee_connection_request",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(index=True)
    requested_by: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_agents.id"), index=True
    )
    request_id: Mapped[UUID] = mapped_column()
    command_digest: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
