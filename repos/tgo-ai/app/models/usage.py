"""Usage tracking models for analytics and monitoring."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.project import Project


class ToolUsageRecord(BaseModel):
    (
        '\n'
        '    Tool usage records for tracking AI agent tool usage.\n'
        '    \n'
        '    Tracks when agents use tools, their parameters, results,\n'
        '    and performance metrics for analytics and monitoring.\n'
        '    '
    )

    __tablename__ = "ai_tool_usage_records"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Associated project ID (logical reference to API service)",
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_agents.id", ondelete="CASCADE"),
        nullable=False,
        comment="Agent that used the tool",
    )

    tool_name: Mapped[str] = mapped_column(
        String(355),
        nullable=False,
        comment=(
            'Tool name with provider prefix (format: '
            '"tool_provider:tool_name")'
        ),
    )

    session_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Session or conversation identifier",
    )

    user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="User or visitor identifier who triggered the tool usage",
    )

    # Tool execution details
    input_parameters: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Parameters passed to the tool (JSON format)",
    )

    execution_result: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Result returned by the tool (JSON format)",
    )

    execution_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="Execution status: pending, success, error, timeout",
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if execution failed",
    )

    # Performance metrics
    execution_duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Tool execution duration in milliseconds",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When tool execution started",
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When tool execution completed",
    )

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project",
        primaryjoin="foreign(ToolUsageRecord.project_id) == Project.id",
        lazy="selectin",
    )
    agent: Mapped["Agent"] = relationship("Agent", lazy="selectin")

    def __repr__(self) -> str:
        """String representation of the tool usage record."""
        return (
            f'<ToolUsageRecord(id='
            f'{self.id}'
            f", tool_name='"
            f'{self.tool_name}'
            f"', status='"
            f'{self.execution_status}'
            f"')>"
        )

    @property
    def tool_provider(self) -> str:
        """Extract tool provider from tool_name string."""
        if ":" in self.tool_name:
            return self.tool_name.split(":", 1)[0]
        return "unknown"

    @property
    def tool_name_only(self) -> str:
        """Extract tool name from tool_name string."""
        if ":" in self.tool_name:
            return self.tool_name.split(":", 1)[1]
        return self.tool_name


class CollectionUsageRecord(BaseModel):
    """
    Collection usage records for tracking RAG analytics.

    Tracks when agents access document collections for knowledge
    retrieval, including query details and performance metrics.
    """

    __tablename__ = "ai_collection_usage_records"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Associated project ID (logical reference to API service)",
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_agents.id", ondelete="CASCADE"),
        nullable=False,
        comment="Agent that accessed the collection",
    )

    collection_id: Mapped[str] = mapped_column(
        String(36),  # UUID string length
        nullable=False,
        comment=(
            'Collection ID (UUID string) - references external RAG service'
        ),
    )

    session_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Session or conversation identifier",
    )

    user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="User or visitor identifier who triggered the query",
    )

    # Query details
    query_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Search query or prompt text",
    )

    query_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="semantic_search",
        comment="Type of query: semantic_search, keyword_search, hybrid",
    )

    query_parameters: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Query parameters (filters, limits, etc.) in JSON format",
    )

    # Results and performance
    documents_retrieved: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of document chunks retrieved",
    )

    retrieved_documents: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Retrieved document chunks with metadata and scores",
    )

    max_relevance_score: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="Highest relevance score from retrieved documents",
    )

    avg_relevance_score: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="Average relevance score from retrieved documents",
    )

    # Performance metrics
    query_duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Query execution duration in milliseconds",
    )

    # Query status and results
    query_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="Query status: pending, success, error, timeout",
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if query failed",
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When query execution started",
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When query execution completed",
    )

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project",
        primaryjoin="foreign(CollectionUsageRecord.project_id) == Project.id",
        lazy="selectin",
    )
    agent: Mapped["Agent"] = relationship("Agent", lazy="selectin")

    def __repr__(self) -> str:
        """String representation of the collection usage record."""
        return (
            f'<CollectionUsageRecord(id='
            f'{self.id}'
            f', collection_id='
            f'{self.collection_id}'
            f", status='"
            f'{self.query_status}'
            f"')>"
        )


class AgentUsageRecord(BaseModel):
    """
    Agent usage records for tracking performance metrics.

    Aggregated statistics about agent usage, performance,
    and error rates for monitoring and analytics.
    """

    __tablename__ = "ai_agent_usage_records"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Associated project ID (logical reference to API service)",
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_agents.id", ondelete="CASCADE"),
        nullable=False,
        comment="Agent being tracked",
    )

    # Core tracking fields
    request_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total number of requests made by the agent",
    )

    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of successful requests",
    )

    failure_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of failed requests",
    )

    avg_response_time_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Average response time in milliseconds",
    )

    last_request_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Most recent request timestamp",
    )

    # Time-based aggregation
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Start of the tracking period",
    )

    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="End of the tracking period",
    )

    aggregation_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Type of aggregation: hourly, daily, weekly, monthly",
    )

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project",
        primaryjoin="foreign(AgentUsageRecord.project_id) == Project.id",
        lazy="selectin",
    )
    agent: Mapped["Agent"] = relationship("Agent", lazy="selectin")

    def __repr__(self) -> str:
        """String representation of the agent usage record."""
        return (
            f'<AgentUsageRecord(id='
            f'{self.id}'
            f', agent_id='
            f'{self.agent_id}'
            f", period='"
            f'{self.aggregation_type}'
            f"')>"
        )
