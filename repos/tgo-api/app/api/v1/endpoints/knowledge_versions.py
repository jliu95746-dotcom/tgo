"""Tenant-scoped knowledge updates with administrator-only publication."""
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.security import get_current_active_user
from app.models.staff import Staff
from app.schemas.knowledge_versions import (
    SourceKind, VersionChangeRequest, VersionHistoryResponse,
    VersionMaintenanceRequest, VersionMaintenanceResponse, VersionResponse,
)
from app.services.rag_client import rag_client

router = APIRouter()


def require_admin(user: Staff) -> None:
    if user.role != 'admin':
        raise HTTPException(403, '只有管理员可以生效、恢复、停用或清理知识版本。')


@router.get('/{kind}/{reference}', response_model=VersionHistoryResponse)
async def history(kind: SourceKind, reference: UUID,
                  user: Staff = Depends(get_current_active_user)) -> VersionHistoryResponse:
    result = await rag_client.knowledge_version_request('GET', f'/{kind}/{reference}', str(user.project_id))
    return cast(VersionHistoryResponse, VersionHistoryResponse.model_validate(result))


@router.post('/{kind}/{reference}', response_model=VersionResponse)
async def change(kind: SourceKind, reference: UUID, data: VersionChangeRequest,
                 user: Staff = Depends(get_current_active_user)) -> VersionResponse:
    if data.action == 'publish':
        require_admin(user)
    payload = data.model_dump(mode='json', exclude_unset=True)
    payload['actor'] = user.username
    result = await rag_client.knowledge_version_request('POST', f'/{kind}/{reference}', str(user.project_id), payload)
    return cast(VersionResponse, VersionResponse.model_validate(result))


@router.post('/versions/{version_id}/{operation}', response_model=VersionResponse)
async def transition(version_id: UUID, operation: Literal['publish', 'restore', 'submit', 'discard'],
                     user: Staff = Depends(get_current_active_user)) -> VersionResponse:
    if operation in {'publish', 'restore', 'discard'}:
        require_admin(user)
    result = await rag_client.knowledge_version_request(
        'POST', f'/versions/{version_id}/{operation}', str(user.project_id), {'actor': user.username})
    return cast(VersionResponse, VersionResponse.model_validate(result))


@router.post('/{kind}/{reference}/disable', response_model=VersionHistoryResponse)
async def disable(kind: SourceKind, reference: UUID,
                  user: Staff = Depends(get_current_active_user)) -> VersionHistoryResponse:
    require_admin(user)
    result = await rag_client.knowledge_version_request(
        'POST', f'/{kind}/{reference}/disable', str(user.project_id), {'actor': user.username})
    return cast(VersionHistoryResponse, VersionHistoryResponse.model_validate(result))


@router.post('/{kind}/{reference}/cleanup', response_model=VersionMaintenanceResponse)
async def cleanup(kind: SourceKind, reference: UUID, data: VersionMaintenanceRequest,
                  user: Staff = Depends(get_current_active_user)) -> VersionMaintenanceResponse:
    require_admin(user)
    result = await rag_client.knowledge_version_request(
        'POST', f'/{kind}/{reference}/cleanup', str(user.project_id), data.model_dump(mode='json'))
    return cast(VersionMaintenanceResponse, VersionMaintenanceResponse.model_validate(result))


@router.post('/{kind}/{reference}/enable', response_model=VersionHistoryResponse)
async def enable(kind: SourceKind, reference: UUID,
                 user: Staff = Depends(get_current_active_user)) -> VersionHistoryResponse:
    require_admin(user)
    result = await rag_client.knowledge_version_request(
        'POST', f'/{kind}/{reference}/enable', str(user.project_id), {'actor': user.username})
    return cast(VersionHistoryResponse, VersionHistoryResponse.model_validate(result))


@router.post('/file/{reference}/upload')
async def upload(reference: UUID, file: UploadFile = File(...),
                 user: Staff = Depends(get_current_active_user)) -> dict[str, object]:
    return cast(dict[str, object], await rag_client.upload_knowledge_replacement(str(user.project_id), str(reference), user.username, file))
