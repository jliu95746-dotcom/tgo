"""Staff schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.staff import StaffRole, StaffStatus
from app.schemas.base import BaseSchema, PaginatedResponse, SoftDeleteMixin, TimestampMixin


class StaffBase(BaseSchema):
    """Base staff schema."""
    
    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Staff username for login"
    )
    name: Optional[str] = Field(
        None,
        max_length=100,
        description="Staff real name"
    )
    nickname: Optional[str] = Field(
        None,
        max_length=100,
        description="Staff display name"
    )
    avatar_url: Optional[str] = Field(
        None,
        max_length=255,
        description="Staff avatar URL"
    )
    description: Optional[str] = Field(
        None,
        max_length=500,
        description="Staff description for LLM assignment prompts"
    )
    role: StaffRole = Field(
        default=StaffRole.USER,
        description="Staff role: user or agent"
    )
    status: StaffStatus = Field(
        default=StaffStatus.OFFLINE,
        description="Staff status: online, offline, busy"
    )
    is_active: bool = Field(
        default=True,
        description="Whether staff is active for service (long-term switch)"
    )
    service_paused: bool = Field(
        default=False,
        description="Whether staff has temporarily paused accepting new visitors (short-term switch)"
    )


class StaffCreate(StaffBase):
    """Schema for creating a staff member."""
    
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Staff password (will be hashed)"
    )
    agent_id: Optional[UUID] = Field(
        None,
        description="Required when role=agent. References AI agent from AI Service"
    )


class StaffUpdate(BaseSchema):
    """Schema for updating a staff member."""
    
    name: Optional[str] = Field(
        None,
        max_length=100,
        description="Updated real name"
    )
    nickname: Optional[str] = Field(
        None,
        max_length=100,
        description="Updated display name"
    )
    avatar_url: Optional[str] = Field(
        None,
        max_length=255,
        description="Updated avatar URL"
    )
    description: Optional[str] = Field(
        None,
        max_length=500,
        description="Updated description for LLM assignment prompts"
    )
    role: Optional[StaffRole] = Field(
        None,
        description="Updated role (requires agent_id validation for agent role)"
    )
    agent_id: Optional[UUID] = Field(
        None,
        description="Updated agent reference (validated with AI Service)"
    )
    status: Optional[StaffStatus] = Field(
        None,
        description="Updated status"
    )
    is_active: Optional[bool] = Field(
        None,
        description="Whether staff is active for service (long-term switch)"
    )
    service_paused: Optional[bool] = Field(
        None,
        description="Whether staff has temporarily paused accepting new visitors (short-term switch)"
    )
    password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=128,
        description="Updated password (will be hashed)"
    )


class StaffInDB(StaffBase, TimestampMixin, SoftDeleteMixin):
    """Schema for staff in database."""
    
    id: UUID = Field(..., description="Staff ID")
    project_id: UUID = Field(..., description="Associated project ID")
    password_hash: str = Field(..., description="Hashed password")


class StaffResponse(BaseSchema):
    account_enabled: bool = Field(default=True)
    email_verified_at: Optional[datetime] = Field(default=None)
    """Schema for staff response (excludes password_hash)."""
    
    id: UUID = Field(..., description="Staff ID")
    project_id: UUID = Field(..., description="Associated project ID")
    username: str = Field(..., description="Staff username")
    name: Optional[str] = Field(None, description="Staff real name")
    nickname: Optional[str] = Field(None, description="Staff display name")
    avatar_url: Optional[str] = Field(None, description="Staff avatar URL")
    description: Optional[str] = Field(None, description="Staff description for LLM assignment prompts")
    role: StaffRole = Field(..., description="Staff role")
    status: StaffStatus = Field(..., description="Staff status")
    is_active: bool = Field(..., description="Whether staff is active for service (long-term switch)")
    service_paused: bool = Field(..., description="Whether staff has temporarily paused accepting new visitors (short-term switch)")
    is_working: Optional[bool] = Field(
        None, 
        description="Whether staff is currently within service hours (computed field based on VisitorAssignmentRule)"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    deleted_at: Optional[datetime] = Field(None, description="Soft deletion timestamp")


class StaffListParams(BaseSchema):
    """Parameters for listing staff members."""
    
    role: Optional[StaffRole] = Field(
        None,
        description="Filter staff by role"
    )
    status: Optional[StaffStatus] = Field(
        None,
        description="Filter staff by status"
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of staff members to return"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of staff members to skip"
    )


class StaffListResponse(PaginatedResponse):
    """Schema for staff list response."""
    
    data: list[StaffResponse] = Field(..., description="List of staff members")


class StaffLogin(BaseSchema):
    """Schema for staff login."""
    
    username: str = Field(..., description="Staff username")
    password: str = Field(..., description="Staff password")


class StaffLoginResponse(BaseSchema):
    """Schema for staff login response."""
    
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration time in seconds")
    staff: StaffResponse = Field(..., description="Staff information")
