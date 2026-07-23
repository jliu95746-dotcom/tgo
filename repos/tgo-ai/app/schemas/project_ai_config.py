"""Pydantic schemas for ProjectAIConfig sync API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class ProjectAIConfigUpsert(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "project_id": "00000000-0000-0000-0000-000000000000",
                "default_chat_provider_id": "11111111-1111-1111-1111-111111111111",
                "default_chat_model": "gpt-4o",
                "default_embedding_provider_id": "22222222-2222-2222-2222-222222222222",
                "default_embedding_model": "text-embedding-3-large"
            }
        ]
    })

    # Primary key
    project_id: UUID = Field(..., description="Project UUID (primary key)")

    # Optional defaults
    default_chat_provider_id: Optional[UUID] = Field(None)
    default_chat_model: Optional[str] = Field(None, max_length=150)

    default_embedding_provider_id: Optional[UUID] = Field(None)
    default_embedding_model: Optional[str] = Field(None, max_length=150)

    default_asr_provider_id: Optional[UUID] = Field(None)
    default_asr_model: Optional[str] = Field(None, max_length=150)
    default_ocr_provider_id: Optional[UUID] = Field(None)
    default_ocr_model: Optional[str] = Field(None, max_length=150)
    default_vlm_provider_id: Optional[UUID] = Field(None)
    default_vlm_model: Optional[str] = Field(None, max_length=150)


class ProjectAIConfigResponse(BaseModel):
    project_id: str
    default_chat_provider_id: Optional[str] = None
    default_chat_model: Optional[str] = None
    default_embedding_provider_id: Optional[str] = None
    default_embedding_model: Optional[str] = None
    default_asr_provider_id: Optional[str] = None
    default_asr_model: Optional[str] = None
    default_ocr_provider_id: Optional[str] = None
    default_ocr_model: Optional[str] = None
    default_vlm_provider_id: Optional[str] = None
    default_vlm_model: Optional[str] = None
    # Sync tracking fields
    last_sync_at: Optional[datetime] = None
    sync_status: Optional[str] = None
    sync_error: Optional[str] = None
    sync_attempt_count: Optional[int] = None

    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_model(cls, m) -> "ProjectAIConfigResponse":
        return cls(
            project_id=str(m.project_id),
            default_chat_provider_id=str(m.default_chat_provider_id) if m.default_chat_provider_id else None,
            default_chat_model=m.default_chat_model,
            default_embedding_provider_id=str(m.default_embedding_provider_id) if m.default_embedding_provider_id else None,
            default_embedding_model=m.default_embedding_model,
            default_asr_provider_id=str(m.default_asr_provider_id) if m.default_asr_provider_id else None,
            default_asr_model=m.default_asr_model,
            default_ocr_provider_id=str(m.default_ocr_provider_id) if m.default_ocr_provider_id else None,
            default_ocr_model=m.default_ocr_model,
            default_vlm_provider_id=str(m.default_vlm_provider_id) if m.default_vlm_provider_id else None,
            default_vlm_model=m.default_vlm_model,
            last_sync_at=m.last_sync_at,
            sync_status=m.sync_status,
            sync_error=m.sync_error,
            sync_attempt_count=m.sync_attempt_count,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )


class ProjectAIConfigSyncRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "configs": [
                    {
                        "project_id": "00000000-0000-0000-0000-000000000000",
                        "default_chat_provider_id": "11111111-1111-1111-1111-111111111111",
                        "default_chat_model": "gpt-4o",
                        "default_embedding_provider_id": "22222222-2222-2222-2222-222222222222",
                        "default_embedding_model": "text-embedding-3-large"
                    }
                ]
            }
        ]
    })
    configs: List[ProjectAIConfigUpsert]


class ProjectAIConfigSyncResponse(BaseModel):
    data: List[ProjectAIConfigResponse]

