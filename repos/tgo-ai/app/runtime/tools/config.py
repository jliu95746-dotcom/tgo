"""工具智能体运行时配置."""

from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelSettings(BaseSettings):
    """默认模型配置."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLS_RUNTIME__MODEL__",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    name: str = Field(default="openai:gpt-4o", description="默认模型标识")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(
        default=4000, ge=100, le=16_000, description="最大生成token"
    )
    system_prompt: str = Field(
        default="You are a helpful assistant that has access to a variety of tools.",
        description="默认系统提示词",
    )


class MCPSettings(BaseSettings):
    """MCP服务配置."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLS_RUNTIME__MCP__",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=False, description="是否启用MCP服务")
    default_url: str | None = Field(default=None, description="默认MCP服务URL")


class RAGSettings(BaseSettings):
    """RAG服务配置."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLS_RUNTIME__RAG__",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=False, description="是否启用RAG服务")
    default_url: str | None = Field(default=None, description="默认RAG服务URL")
    api_key: Optional[str] = Field(default=None, description="RAG服务默认API Key")


class SupabaseSettings(BaseSettings):
    """Supabase访问配置."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLS_RUNTIME__SUPABASE__",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    url: Optional[str] = Field(default=None, description="Supabase项目URL")
    key: Optional[str] = Field(
        default=None, description="Supabase Service Role Key"
    )
    access_token: Optional[str] = Field(
        default=None, description="Supabase访问令牌"
    )


class ToolsRuntimeSettings(BaseSettings):
    """工具智能体运行时配置聚合."""

    model_config = SettingsConfigDict(
        env_prefix="TOOLS_RUNTIME__",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    model: ModelSettings = Field(default_factory=ModelSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    supabase: SupabaseSettings = Field(default_factory=SupabaseSettings)
