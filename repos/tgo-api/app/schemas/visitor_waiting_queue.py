"""Visitor Waiting Queue schemas."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, PaginationMetadata


class WaitingStatusEnum(str, Enum):
    """Waiting queue status enumeration."""
    
    WAITING = "waiting"
    ASSIGNED = "assigned"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class QueueSourceEnum(str, Enum):
    """Queue entry source enumeration."""
    
    AI_REQUEST = "ai_request"
    VISITOR_REQUEST = "visitor"
    TRANSFER = "transfer"
    SYSTEM = "system"
    NO_STAFF = "no_staff"


class QueueUrgencyEnum(str, Enum):
    """Queue urgency level enumeration."""
    
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# ============================================================================
# Response Schemas
# ============================================================================

class VisitorBriefResponse(BaseSchema):
    """Brief visitor info for queue response."""
    
    id: UUID = Field(..., description="Visitor ID")
    name: Optional[str] = Field(None, description="Visitor name")
    nickname: Optional[str] = Field(None, description="Visitor nickname")
    avatar_url: Optional[str] = Field(None, description="Visitor avatar URL")
    platform_open_id: str = Field(..., description="Platform open ID")


class StaffBriefResponse(BaseSchema):
    """Brief staff info for queue response."""
    
    id: UUID = Field(..., description="Staff ID")
    name: Optional[str] = Field(None, description="Staff name")
    username: str = Field(..., description="Staff username")
    avatar_url: Optional[str] = Field(None, description="Staff avatar URL")


class WaitingQueueResponse(BaseSchema):
    """Response schema for a waiting queue entry."""
    
    id: UUID = Field(..., description="Queue entry ID")
    project_id: UUID = Field(..., description="Project ID")
    visitor_id: UUID = Field(..., description="Visitor ID")
    session_id: Optional[UUID] = Field(None, description="Associated session ID")
    assigned_staff_id: Optional[UUID] = Field(None, description="Assigned staff ID")
    
    source: str = Field(..., description="Queue entry source")
    urgency: str = Field(..., description="Urgency level")
    position: int = Field(..., description="Queue position")
    priority: int = Field(..., description="Priority level")
    status: str = Field(..., description="Queue status")
    
    visitor_message: Optional[str] = Field(None, description="Visitor message")
    reason: Optional[str] = Field(None, description="Queue entry reason")
    channel_id: Optional[str] = Field(None, description="Channel ID")
    channel_type: Optional[int] = Field(None, description="Channel type")
    
    retry_count: Optional[int] = Field(
        default=None, description="Unavailable: assignment attempts are not counted"
    )
    wait_duration_seconds: Optional[int] = Field(None, description="Wait duration in seconds")
    
    entered_at: datetime = Field(..., description="Queue entry time")
    assigned_at: Optional[datetime] = Field(None, description="Assignment time")
    exited_at: Optional[datetime] = Field(None, description="Queue exit time")
    created_at: datetime = Field(..., description="Created timestamp")
    updated_at: datetime = Field(..., description="Updated timestamp")


class WaitingQueueDetailResponse(WaitingQueueResponse):
    """Detailed response including visitor and staff info."""
    
    visitor: Optional[VisitorBriefResponse] = Field(None, description="Visitor details")
    assigned_staff: Optional[StaffBriefResponse] = Field(None, description="Assigned staff details")
    extra_metadata: Optional[Dict[str, Any]] = Field(None, description="Extra metadata")


class WaitingQueueListResponse(BaseSchema):
    """Paginated list of waiting queue entries."""
    
    items: List[WaitingQueueDetailResponse] = Field(
        default_factory=list,
        description="List of waiting queue entries"
    )
    pagination: PaginationMetadata


# ============================================================================
# Request Schemas
# ============================================================================

class WaitingQueueListParams(BaseSchema):
    """Query parameters for listing waiting queue."""
    
    status: Optional[WaitingStatusEnum] = Field(
        None,
        description="Filter by status"
    )
    source: Optional[QueueSourceEnum] = Field(
        None,
        description="Filter by source"
    )
    urgency: Optional[QueueUrgencyEnum] = Field(
        None,
        description="Filter by urgency"
    )
    visitor_id: Optional[UUID] = Field(
        None,
        description="Filter by visitor ID"
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of items per page"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip"
    )


class AcceptVisitorRequest(BaseSchema):
    """Request schema for accepting a visitor from queue."""
    
    visitor_id: UUID = Field(..., description="Visitor ID to accept")
    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional notes for the assignment"
    )


class AcceptVisitorResponse(BaseSchema):
    """Response schema after accepting a visitor."""
    
    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Result message")
    entry_id: Optional[UUID] = Field(None, description="Queue entry ID (if accepted from queue)")
    visitor_id: UUID = Field(..., description="Visitor ID")
    staff_id: UUID = Field(..., description="Assigned staff ID")
    session_id: Optional[UUID] = Field(None, description="Session ID")
    channel_id: Optional[str] = Field(None, description="Channel ID for communication")
    channel_type: Optional[int] = Field(None, description="Channel type")
    wait_duration_seconds: Optional[int] = Field(None, description="How long visitor waited")
