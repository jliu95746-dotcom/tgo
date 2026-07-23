"""Project-level default AI model configuration (synced from tgo-api).

This table stores per-project default chat, embedding, and multimodal model selections.
Design notes:
- Primary key is project_id (UUID)
- No foreign key constraints to ai_projects or ai_llm_providers (sync order independent)
- Only created_at / updated_at timestamps (no soft delete by default)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.base import BaseModel


class _BaseNoId(DeclarativeBase):
    """Declarative base bound to the shared BaseModel metadata without id mixin."""

    metadata = BaseModel.metadata


class ProjectAIConfig(_BaseNoId):
    __tablename__ = "ai_project_ai_configs"

    # Primary key - references project but does NOT enforce foreign key constraint
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, comment="Project UUID (primary key)"
    )

    # Default chat model configuration
    default_chat_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="LLM provider id for default chat model"
    )
    default_chat_model: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True, comment="Default chat model name (e.g., 'gpt-4o')"
    )

    # Default embedding model configuration
    default_embedding_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="LLM provider id for default embedding model"
    )
    default_embedding_model: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True, comment="Default embedding model name"
    )

    # Default multimodal model configuration
    default_asr_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Provider id for default ASR model"
    )
    default_asr_model: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True, comment="Default ASR model name"
    )
    default_ocr_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Provider id for default OCR model"
    )
    default_ocr_model: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True, comment="Default OCR model name"
    )
    default_vlm_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Provider id for default VLM model"
    )
    default_vlm_model: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True, comment="Default VLM model name"
    )

    # Sync tracking fields for embedding configs -> RAG service
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Timestamp of last successful sync to RAG"
    )
    sync_status: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default="not_synced", comment="Current sync status: pending|success|failed|not_synced"
    )
    sync_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Last sync error message, if any"
    )
    sync_attempt_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=0, comment="Number of sync attempts for current config"
    )

    # Standard audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectAIConfig(project_id={self.project_id}, "
            f"chat={self.default_chat_provider_id}:{self.default_chat_model}, "
            f"embedding={self.default_embedding_provider_id}:{self.default_embedding_model}, "
            f"asr={self.default_asr_provider_id}:{self.default_asr_model}, "
            f"ocr={self.default_ocr_provider_id}:{self.default_ocr_model}, "
            f"vlm={self.default_vlm_provider_id}:{self.default_vlm_model})>"
        )

