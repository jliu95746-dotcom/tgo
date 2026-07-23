"""Strict API contracts for the customer logistics archive."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LogisticsSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )


class LogisticsSettingsUpdate(LogisticsSchema):
    enabled: bool = True
    auto_capture_visitor_messages: bool = True
    auto_capture_staff_messages: bool = True
    verify_before_binding: bool = True
    auto_query_on_mention: bool = True
    query_tool_id: UUID | None = None
    poll_interval_minutes: int = Field(default=360, ge=5, le=10080)
    stop_after_delivered: bool = True
    archive_after_days: int = Field(default=30, ge=1, le=3650)
    conflict_policy: Literal["manual_review", "keep_first"] = "manual_review"


class LogisticsSettingsResponse(LogisticsSettingsUpdate):
    id: UUID | None = None
    project_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ShipmentCreateRequest(LogisticsSchema):
    tracking_no: str = Field(min_length=8, max_length=64)
    carrier_code: str | None = Field(default=None, max_length=64)
    carrier_name: str | None = Field(default=None, max_length=128)


class ShipmentResponse(LogisticsSchema):
    id: UUID
    visitor_id: UUID
    tracking_no_masked: str
    carrier_code: str | None
    carrier_name: str | None
    status: Literal[
        "unknown", "pending", "active", "in_transit", "delivered", "exception"
    ]
    source: Literal["visitor_message", "staff_message", "manual", "order_sync"]
    verification_state: Literal["pending", "verified", "conflict"]
    latest_summary: str | None
    last_checked_at: datetime | None
    delivered_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ShipmentListResponse(LogisticsSchema):
    shipments: tuple[ShipmentResponse, ...]


class TrackingEventResponse(LogisticsSchema):
    id: UUID
    shipment_id: UUID
    status: str | None
    description: str
    location: str | None
    event_time: datetime


class TrackingEventListResponse(LogisticsSchema):
    events: tuple[TrackingEventResponse, ...]


class ShipmentQueryResponse(LogisticsSchema):
    shipment: ShipmentResponse
    events: tuple[TrackingEventResponse, ...]
    queried_live: bool
    message: str


class LogisticsToolTestRequest(LogisticsSchema):
    tracking_no: str = Field(min_length=8, max_length=64)


class LogisticsToolTestResponse(LogisticsSchema):
    success: bool
    message: str
    preview: str | None = None
