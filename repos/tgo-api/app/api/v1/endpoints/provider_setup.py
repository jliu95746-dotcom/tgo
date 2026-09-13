"""Non-persistent setup previews. Secrets never appear in responses."""
import httpx
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import AIProvider, Staff
from app.schemas.provider_setup import ProviderConnectionDraft, ProviderModelProbe, ModelProbeResult
from app.schemas.remote_model import RemoteModelListResponse, RemoteModelInfo
from app.services.provider_setup import resolve_connection, run_model_probe
from app.schemas.media_probe import MediaProbeResult
from app.services.ai_client import ai_client

router = APIRouter()


@router.post('/probe-media', response_model=MediaProbeResult)
async def probe_media(
    provider_id: Annotated[UUID, Form()],
    model_id: Annotated[str, Form(min_length=1, max_length=150)],
    capability: Annotated[Literal['asr', 'ocr', 'vlm'], Form()],
    file: Annotated[UploadFile, File()],
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> MediaProbeResult:
    try:
        item = db.query(AIProvider).filter(
            AIProvider.id == provider_id,
            AIProvider.project_id == current_user.project_id,
            AIProvider.deleted_at.is_(None), AIProvider.is_active.is_(True),
        ).first()
        if item is None:
            raise HTTPException(404, '模型服务不存在或未启用')
        content = await file.read(5 * 1024 * 1024 + 1)
        if not content or len(content) > 5 * 1024 * 1024:
            raise HTTPException(422, '请选择不超过 5 MB 的非空测试文件')
        return await ai_client.probe_media_model(
            project_id=str(current_user.project_id), provider_id=str(provider_id),
            model_id=model_id, capability=capability, content=content,
            mime_type=file.content_type or 'application/octet-stream',
        )
    finally:
        await file.close()


@router.post('/probe-model', response_model=ModelProbeResult)
async def probe_model(payload: ProviderModelProbe, db: Session = Depends(get_db), current_user: Staff = Depends(get_current_active_user)):
    connection = resolve_connection(db, current_user.project_id, payload)
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        return await run_model_probe(connection, payload, client)


@router.post('/preview-models', response_model=RemoteModelListResponse)
async def preview_models(payload: ProviderConnectionDraft, db: Session = Depends(get_db), current_user: Staff = Depends(get_current_active_user)):
    from app.api.v1.endpoints.ai_providers import _build_test_request, _parse_remote_models
    connection = resolve_connection(db, current_user.project_id, payload)
    item = AIProvider(provider=connection.provider, api_base_url=connection.base, config=connection.config)
    if connection.provider == 'ollama':
        url = connection.base.removesuffix('/v1') + '/api/tags'
        method, headers = 'GET', {}
    else:
        method, url, headers = _build_test_request(item, connection.key)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.request(method, url, headers=headers)
        if not response.is_success:
            raise HTTPException(502, f'获取模型失败：HTTP {response.status_code}，可检查配置或手动添加模型')
        data = response.json()
        if connection.provider == 'ollama':
            models = [RemoteModelInfo(id=m['name'], name=m['name'], model_type='embedding' if 'embed' in m['name'] else 'chat') for m in data.get('models', [])]
        else:
            parser = connection.provider if connection.provider in ('anthropic', 'claude', 'azure', 'azure_openai') else 'openai'
            models = _parse_remote_models(parser, data)
        return RemoteModelListResponse(provider=connection.provider, models=models, is_fallback=False)
    except (httpx.RequestError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(502, '获取模型失败，请检查网络或手动添加模型') from None
