"""Service for syncing project-level default AI model configs from tgo-api."""

from __future__ import annotations

import uuid
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_ai_config import ProjectAIConfig
from app.services.rag_embedding_sync_service import (
    build_embedding_configs,
    fire_and_forget_embedding_sync,
)


class ProjectAIConfigService:
    """Application service for ProjectAIConfig upsert and retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, project_id: uuid.UUID) -> Optional[ProjectAIConfig]:
        stmt = select(ProjectAIConfig).where(ProjectAIConfig.project_id == project_id)
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

    async def upsert_config(
        self,
        *,
        project_id: uuid.UUID,
        default_chat_provider_id: Optional[uuid.UUID] = None,
        default_chat_model: Optional[str] = None,
        default_embedding_provider_id: Optional[uuid.UUID] = None,
        default_embedding_model: Optional[str] = None,
        default_asr_provider_id: Optional[uuid.UUID] = None,
        default_asr_model: Optional[str] = None,
        default_ocr_provider_id: Optional[uuid.UUID] = None,
        default_ocr_model: Optional[str] = None,
        default_vlm_provider_id: Optional[uuid.UUID] = None,
        default_vlm_model: Optional[str] = None,
    ) -> ProjectAIConfig:
        existing = await self.get(project_id)
        if existing:
            existing.default_chat_provider_id = default_chat_provider_id
            existing.default_chat_model = default_chat_model
            existing.default_embedding_provider_id = default_embedding_provider_id
            existing.default_embedding_model = default_embedding_model
            existing.default_asr_provider_id = default_asr_provider_id
            existing.default_asr_model = default_asr_model
            existing.default_ocr_provider_id = default_ocr_provider_id
            existing.default_ocr_model = default_ocr_model
            existing.default_vlm_provider_id = default_vlm_provider_id
            existing.default_vlm_model = default_vlm_model
            # mark pending for new sync cycle
            existing.sync_status = "pending"
            existing.sync_error = None
            existing.sync_attempt_count = 0
            await self.db.flush()
            await self.db.refresh(existing)
            await self.db.commit()  # ensure visibility for background session
            # Build and dispatch embedding sync (fire-and-forget)
            configs = await build_embedding_configs(self.db, [existing])
            if configs:
                fire_and_forget_embedding_sync(configs)
            return existing

        cfg = ProjectAIConfig(
            project_id=project_id,
            default_chat_provider_id=default_chat_provider_id,
            default_chat_model=default_chat_model,
            default_embedding_provider_id=default_embedding_provider_id,
            default_embedding_model=default_embedding_model,
            default_asr_provider_id=default_asr_provider_id,
            default_asr_model=default_asr_model,
            default_ocr_provider_id=default_ocr_provider_id,
            default_ocr_model=default_ocr_model,
            default_vlm_provider_id=default_vlm_provider_id,
            default_vlm_model=default_vlm_model,
            sync_status="pending",
            sync_error=None,
            sync_attempt_count=0,
        )
        self.db.add(cfg)
        await self.db.flush()
        await self.db.refresh(cfg)
        await self.db.commit()  # ensure visibility for background session
        # Build and dispatch embedding sync (fire-and-forget)
        configs = await build_embedding_configs(self.db, [cfg])
        if configs:
            fire_and_forget_embedding_sync(configs)
        return cfg

    async def sync_configs(self, configs: Iterable[dict]) -> List[ProjectAIConfig]:
        synced: list[ProjectAIConfig] = []
        for payload in configs:
            cfg = await self.upsert_config(
                project_id=payload["project_id"],
                default_chat_provider_id=payload.get("default_chat_provider_id"),
                default_chat_model=payload.get("default_chat_model"),
                default_embedding_provider_id=payload.get("default_embedding_provider_id"),
                default_embedding_model=payload.get("default_embedding_model"),
                default_asr_provider_id=payload.get("default_asr_provider_id"),
                default_asr_model=payload.get("default_asr_model"),
                default_ocr_provider_id=payload.get("default_ocr_provider_id"),
                default_ocr_model=payload.get("default_ocr_model"),
                default_vlm_provider_id=payload.get("default_vlm_provider_id"),
                default_vlm_model=payload.get("default_vlm_model"),
            )
            synced.append(cfg)
        # upsert_config already commits; no additional commit required here
        # Build and dispatch a single batch embedding sync for all updated projects
        embed_cfgs = await build_embedding_configs(self.db, synced)
        if embed_cfgs:
            fire_and_forget_embedding_sync(embed_cfgs)
        return synced

