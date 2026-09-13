"""Validated plugin result contract shared by AI execution paths."""

from pydantic import BaseModel, Field, JsonValue
from uuid import UUID


class PluginExecutionContext(BaseModel):
    """Plugin context uses visitor IDs, never staff transport identities."""

    visitor_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    language: str | None = None

    @staticmethod
    def visitor_from_user(user_id: str | None) -> str | None:
        if not user_id or user_id.endswith("-staff"):
            return None
        try:
            return str(UUID(user_id.removesuffix("-vtr")))
        except ValueError:
            return None


class PluginExecutionResult(BaseModel):
    """Mirror plugin runtime output without accepting truthy success strings."""

    success: bool = Field(strict=True)
    content: str = ""
    data: dict[str, JsonValue] | None = None
    error: str | None = None
