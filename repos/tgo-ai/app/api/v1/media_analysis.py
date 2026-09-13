"""Authenticated binary media analysis; metadata URIs are never downloaded."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_current_or_internal_project_id, get_db
from app.runtime.multimodal.providers.base import ProviderExecutionError
from app.schemas.multimodal import MediaAnalysisRequest, MediaAnalysisResult
from app.services.configured_multimodal import ConfiguredMultimodalService
from app.schemas.media_probe import MediaProbeResult
from app.schemas.multimodal import AnalysisCapability
from app.services.media_model_probe import MAX_PROBE_BYTES, probe_media_model

router = APIRouter()


@router.post("/probe", response_model=MediaProbeResult)
async def probe_media(
    provider_id: Annotated[uuid.UUID, Form()],
    model_id: Annotated[str, Form(min_length=1, max_length=150)],
    capability: Annotated[AnalysisCapability, Form()],
    file: Annotated[UploadFile, File()],
    project_id: uuid.UUID = Depends(get_current_or_internal_project_id),
    db: AsyncSession = Depends(get_db),
) -> MediaProbeResult:
    try:
        content = await file.read(MAX_PROBE_BYTES + 1)
        if len(content) > MAX_PROBE_BYTES:
            raise HTTPException(413, "测试文件不能超过 5 MB")
        return await probe_media_model(
            db,
            project_id,
            provider_id,
            model_id,
            capability,
            content,
            file.content_type or "application/octet-stream",
        )
    finally:
        await file.close()


@router.post("/media", response_model=MediaAnalysisResult)
async def analyze_media(
    metadata: Annotated[str, Form(max_length=8192)],
    file: Annotated[UploadFile, File()],
    project_id: uuid.UUID = Depends(get_current_or_internal_project_id),
    db: AsyncSession = Depends(get_db),
) -> MediaAnalysisResult:
    """Call only this project's explicit ASR/OCR/VLM defaults with uploaded bytes."""
    try:
        try:
            request = MediaAnalysisRequest.model_validate_json(metadata)
        except ValidationError as exc:
            raise HTTPException(422, "媒体描述不符合格式要求") from exc
        if file.size is not None and file.size > settings.multimodal_max_media_bytes:
            raise HTTPException(413, "媒体文件超过识别大小限制")
        content = await file.read(settings.multimodal_max_media_bytes + 1)
        if len(content) > settings.multimodal_max_media_bytes:
            raise HTTPException(413, "媒体文件超过识别大小限制")
        try:
            return await ConfiguredMultimodalService(db).analyze(
                project_id, request, content
            )
        except ProviderExecutionError as exc:
            raise HTTPException(422, "媒体格式或内容校验失败") from exc
    finally:
        await file.close()
