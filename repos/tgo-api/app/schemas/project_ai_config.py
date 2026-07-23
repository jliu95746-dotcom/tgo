"""Schemas for Project AI default model configuration."""

from typing import Optional
from uuid import UUID
from datetime import datetime

from pydantic import Field

from app.schemas.base import BaseSchema, SoftDeleteMixin, TimestampMixin


class ProjectAIConfigBase(BaseSchema):
    default_chat_provider_id: Optional[UUID] = Field(
        None, description="AIProvider ID for default chat model"
    )
    default_chat_model: Optional[str] = Field(
        None, max_length=100, description="Default chat model identifier"
    )
    default_embedding_provider_id: Optional[UUID] = Field(
        None, description="AIProvider ID for default embedding model"
    )
    default_embedding_model: Optional[str] = Field(
        None, max_length=100, description="Default embedding model identifier"
    )
    default_asr_provider_id: Optional[UUID] = Field(
        None, description="AIProvider ID for default ASR model"
    )
    default_asr_model: Optional[str] = Field(
        None, max_length=100, description="Default ASR model identifier"
    )
    default_ocr_provider_id: Optional[UUID] = Field(
        None, description="AIProvider ID for default OCR model"
    )
    default_ocr_model: Optional[str] = Field(
        None, max_length=100, description="Default OCR model identifier"
    )
    default_vlm_provider_id: Optional[UUID] = Field(
        None, description="AIProvider ID for default VLM model"
    )
    default_vlm_model: Optional[str] = Field(
        None, max_length=100, description="Default VLM model identifier"
    )


class ProjectAIConfigCreate(ProjectAIConfigBase):
    """Create payload for project AI config (used for upsert)."""
    pass


class ProjectAIConfigUpdate(ProjectAIConfigBase):
    """Update payload for project AI config (used for upsert)."""
    pass


class ProjectAIConfigResponse(ProjectAIConfigBase, TimestampMixin, SoftDeleteMixin):
    id: UUID = Field(..., description="Config ID")
    project_id: UUID = Field(..., description="Project ID")
    # Sync tracking fields
    last_synced_at: Optional[datetime] = Field(None, description="Last time synced to AI service")
    sync_status: Optional[str] = Field(None, description="Sync status: pending/synced/failed")
    sync_error: Optional[str] = Field(None, description="Last sync error message, if any")

