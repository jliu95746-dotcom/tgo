"""Tool-related Pydantic schemas."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, ConfigDict, field_validator, field_serializer, JsonValue

from app.schemas.base import BaseSchema, IDMixin, TimestampMixin
from app.models.tool import ToolType, ToolSourceType


class ToolBase(BaseSchema):
    """Base fields for Tool schemas."""

    # Redundant but explicit to satisfy requirement
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID = Field(description="Project ID that owns the tool")
    name: str = Field(description="Tool name")
    title: Optional[str] = Field(default=None, description="Display title for the tool (Legacy/Fallback)")
    title_zh: Optional[str] = Field(default=None, description="Display title for the tool (Chinese)")
    title_en: Optional[str] = Field(default=None, description="Display title for the tool (English)")
    description: Optional[str] = Field(default=None, description="Optional tool description")
    tool_type: ToolType = Field(description="Tool type (MCP | FUNCTION)")
    transport_type: Optional[str] = Field(default=None, description="Transport type (e.g., http, stdio, sse)")
    endpoint: Optional[str] = Field(default=None, description="Endpoint URL or path")
    tool_source_type: ToolSourceType = Field(default=ToolSourceType.LOCAL, description="Tool source (LOCAL or STORE)")
    store_resource_id: Optional[str] = Field(default=None, description="Associated Store resource ID")
    config: Optional[dict] = Field(default=None, description="Tool configuration JSON object")


class ToolResponse(ToolBase, IDMixin, TimestampMixin):
    """Schema for Tool API responses."""

    created_at: datetime = Field(description="Record creation timestamp")
    updated_at: datetime = Field(description="Record last update timestamp")
    deleted_at: Optional[datetime] = Field(default=None, description="Soft delete timestamp")

    @field_serializer("config")
    def redact_logistics_credential(self, config: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        if config and config.get("logistics_provider"):
            from app.services.logistics_provider import public_config
            return {**config, "logistics_provider": public_config(config["logistics_provider"])}
        return config




class ToolCreate(ToolBase):
    """Schema for creating tools."""

    pass


class ToolUpdate(BaseSchema):
    """Schema for updating an existing tool."""

    name: Optional[str] = Field(default=None, description="Updated tool name")
    title: Optional[str] = Field(default=None, description="Updated display title (Legacy/Fallback)")
    title_zh: Optional[str] = Field(default=None, description="Updated display title (Chinese)")
    title_en: Optional[str] = Field(default=None, description="Updated display title (English)")
    description: Optional[str] = Field(default=None, description="Updated tool description")
    tool_type: Optional[ToolType] = Field(default=None, description="Updated tool type (MCP | FUNCTION)")
    transport_type: Optional[str] = Field(default=None, description="Updated transport type (e.g., http, stdio, sse)")
    endpoint: Optional[str] = Field(default=None, description="Updated endpoint URL or path")
    config: Optional[dict] = Field(default=None, description="Updated tool configuration JSON object")

    @field_validator("name", "tool_type", mode="before")
    @classmethod
    def reject_null_required_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("名称和工具类型不能设为空值；不修改时请省略该字段")
        return value


class AgentToolDetail(ToolResponse):
    """Schema for tool details in agent responses, including agent-specific settings."""

    enabled: bool = Field(default=True, description="Whether tool is enabled for this agent")
    permissions: Optional[List[str]] = Field(default=None, description="Tool permissions array for this agent")
    tool_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Agent-specific tool configuration (overrides tool's default config)",
        serialization_alias="agent_tool_config",
    )

