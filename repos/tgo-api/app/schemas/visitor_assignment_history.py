"""Visitor Assignment History schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field

from app.models.visitor_assignment_history import AssignmentSource
from app.schemas.base import BaseSchema, PaginatedResponse


class VisitorAssignmentHistoryBase(BaseSchema):
    """Base schema for visitor assignment history."""

    visitor_id: UUID = Field(..., description="Visitor being assigned")
    assigned_staff_id: Optional[UUID] = Field(
        default=None, description="Staff member assigned"
    )
    previous_staff_id: Optional[UUID] = Field(
        default=None, description="Previous staff member"
    )
    assigned_by_staff_id: Optional[UUID] = Field(
        default=None, description="Staff who initiated assignment"
    )
    source: AssignmentSource = Field(
        default=AssignmentSource.LLM,
        description="Source of assignment: llm, manual, rule, transfer",
    )
    reasoning: Optional[str] = Field(
        default=None, description="Reasoning for assignment decision"
    )
    visitor_message: Optional[str] = Field(
        default=None, description="Visitor's message at assignment time"
    )
    notes: Optional[str] = Field(default=None, description="Additional notes")


class VisitorAssignmentHistoryCreate(VisitorAssignmentHistoryBase):
    """Schema for creating an assignment history record."""

    assignment_rule_id: Optional[UUID] = Field(
        default=None, description="Assignment rule used"
    )
    model_used: Optional[str] = Field(
        default=None, max_length=100, description="LLM model used"
    )
    prompt_used: Optional[str] = Field(
        default=None, description="Prompt sent to LLM"
    )
    llm_response: Optional[str] = Field(
        default=None, description="Full LLM response"
    )
    visitor_context: Optional[Dict[str, Any]] = Field(
        default=None, description="Visitor context"
    )
    candidate_staff_ids: Optional[List[UUID]] = Field(
        default=None, description="Staff candidates considered"
    )
    candidate_scores: Optional[Dict[str, Any]] = Field(
        default=None, description="Candidate scores"
    )
    response_time_ms: Optional[int] = Field(
        default=None, description="LLM response time in ms"
    )
    token_usage: Optional[Dict[str, Any]] = Field(
        default=None, description="Token usage statistics"
    )


class VisitorAssignmentHistoryResponse(BaseSchema):
    """Schema for assignment history response."""

    id: UUID = Field(..., description="History record ID")
    project_id: UUID = Field(..., description="Associated project ID")
    visitor_id: UUID = Field(..., description="Visitor ID")
    assigned_staff_id: Optional[UUID] = Field(
        default=None, description="Assigned staff ID"
    )
    previous_staff_id: Optional[UUID] = Field(
        default=None, description="Previous staff ID"
    )
    assigned_by_staff_id: Optional[UUID] = Field(
        default=None, description="Assigning staff ID"
    )
    assignment_rule_id: Optional[UUID] = Field(
        default=None, description="Assignment rule ID"
    )
    source: AssignmentSource = Field(..., description="Assignment source")
    model_used: Optional[str] = Field(
        default=None, description="LLM model used"
    )
    reasoning: Optional[str] = Field(
        default=None, description="Assignment reasoning"
    )
    visitor_message: Optional[str] = Field(
        default=None, description="Visitor's message"
    )
    visitor_context: Optional[Dict[str, Any]] = Field(
        default=None, description="Visitor context"
    )
    candidate_staff_ids: Optional[List[UUID]] = Field(
        default=None, description="Candidate staff IDs"
    )
    candidate_scores: Optional[Dict[str, Any]] = Field(
        default=None, description="Candidate scores"
    )
    response_time_ms: Optional[int] = Field(
        default=None, description="Response time in ms"
    )
    token_usage: Optional[Dict[str, Any]] = Field(
        default=None, description="Token usage"
    )
    notes: Optional[str] = Field(default=None, description="Notes")
    created_at: datetime = Field(..., description="Assignment timestamp")


class VisitorAssignmentHistoryDetailResponse(VisitorAssignmentHistoryResponse):
    """Detailed response including full LLM interaction data."""

    prompt_used: Optional[str] = Field(
        default=None, description="Prompt sent to LLM"
    )
    llm_response: Optional[str] = Field(
        default=None, description="Full LLM response"
    )


class VisitorAssignmentHistoryListParams(BaseSchema):
    """Parameters for listing assignment history."""

    visitor_id: Optional[UUID] = Field(
        default=None, description="Filter by visitor"
    )
    assigned_staff_id: Optional[UUID] = Field(
        default=None, description="Filter by assigned staff"
    )
    source: Optional[AssignmentSource] = Field(
        default=None, description="Filter by assignment source"
    )
    start_date: Optional[datetime] = Field(
        default=None, description="Filter by start date"
    )
    end_date: Optional[datetime] = Field(
        default=None, description="Filter by end date"
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="Number of records to return"
    )
    offset: int = Field(
        default=0, ge=0, description="Number of records to skip"
    )


class VisitorAssignmentHistoryListResponse(PaginatedResponse):
    """Schema for assignment history list response."""

    data: List[VisitorAssignmentHistoryResponse] = Field(
        ..., description="List of history records"
    )
