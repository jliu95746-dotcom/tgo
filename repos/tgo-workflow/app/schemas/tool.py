from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID


class ToolAvailability(BaseModel):
    """Narrow receipt from the AI service for a tool dependency."""
    model_config = ConfigDict(extra="forbid")
    tool_id: UUID
    project_id: UUID
    available: StrictBool
    workflow_read_only: StrictBool

class ToolExecuteRequest(BaseModel):
    """Direct execution contract accepted by tgo-ai /tools/{id}/execute."""
    model_config = ConfigDict(extra="forbid")
    input_data: Dict[str, JsonValue] = Field(..., description="Input parameters for the tool")

class ToolExecuteResponse(BaseModel):
    """Direct execution response returned by tgo-ai."""
    model_config = ConfigDict(extra="forbid")
    success: StrictBool = Field(..., description="Whether the tool execution was successful")
    output_data: JsonValue = Field(..., description="The tool output, including JSON null")
    error: Optional[str] = Field(None, description="Error message if the execution failed")

class ToolResponse(BaseModel):
    """Schema for Tool API responses, simplified for workflow engine usage."""
    id: str = Field(..., description="Unique identifier of the tool")
    project_id: str = Field(..., description="The ID of the project this tool belongs to")
    name: str = Field(..., description="Name of the tool")
    description: Optional[str] = Field(None, description="Description of the tool")
    tool_type: str = Field(..., description="Type of the tool (e.g., MCP, FUNCTION)")
    # Add other fields if necessary, but these are likely enough for the executor to log or check

