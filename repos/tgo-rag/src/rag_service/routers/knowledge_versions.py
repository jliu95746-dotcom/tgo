"""Internal version operations; the gateway supplies tenant, actor and permissions."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File as Upload, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db_session_dependency
from ..models import File
from ..models.knowledge_governance import KnowledgeGovernanceRecord
from ..models.knowledge_versions import KnowledgeVersion
from ..schemas.knowledge_versions import (
    SourceKind, VersionActorRequest, VersionChangeRequest, VersionHistoryResponse,
    VersionMaintenanceRequest, VersionMaintenanceResponse, VersionResponse, VersionSnapshot,
)
from ..services.knowledge_version_publish import cleanup_history, locked_version, publish, restore
from ..services.knowledge_versions import (
    VersionConflict, VersionNotFound, create_change, ensure_source, history, version_response,
)

router = APIRouter()


@asynccontextmanager
async def errors() -> AsyncIterator[None]:
    try:
        yield
    except VersionNotFound as error:
        raise HTTPException(404, str(error)) from error
    except VersionConflict as error:
        raise HTTPException(409, str(error)) from error


@router.get('/{kind}/{reference}', response_model=VersionHistoryResponse)
async def get_history(kind: SourceKind, reference: UUID, project_id: UUID = Query(...),
                      db: AsyncSession = Depends(get_db_session_dependency)) -> VersionHistoryResponse:
    async with errors():
        return await history(db, project_id, kind, reference)


@router.post('/{kind}/{reference}', response_model=VersionResponse)
async def change(kind: SourceKind, reference: UUID, data: VersionChangeRequest,
                 project_id: UUID = Query(...), db: AsyncSession = Depends(get_db_session_dependency)) -> VersionResponse:
    async with errors():
        version = await create_change(db, project_id, kind, reference, data)
        await db.commit()
        from ..tasks.knowledge_versions import prepare_knowledge_version
        try:
            prepare_knowledge_version.delay(str(project_id), str(version.id), str(version.snapshot['attempt']))
        except Exception:
            await db.refresh(version, with_for_update=True)
            if version.state == 'processing':
                version.state, version.error = 'failed', '处理任务未能提交，请重试。原内容未改变。'
                await db.commit()
        return version_response(version)


@router.post('/versions/{version_id}/{operation}', response_model=VersionResponse)
async def transition(version_id: UUID, operation: Literal['publish', 'restore', 'submit', 'discard'],
                     data: VersionActorRequest, project_id: UUID = Query(...),
                     db: AsyncSession = Depends(get_db_session_dependency)) -> VersionResponse:
    async with errors():
        if operation == 'publish':
            version = await publish(db, project_id, version_id, data.actor)
        elif operation == 'restore':
            version = await restore(db, project_id, version_id, data.actor)
        else:
            version, _, _ = await locked_version(db, project_id, version_id)
            if version.state not in {'ready', 'pending_review', 'failed', 'processing'}:
                raise VersionConflict('当前版本不能执行此操作。')
            if operation == 'submit':
                if version.state != 'ready':
                    raise VersionConflict('只有处理成功的草稿可以提交审核。')
                version.state = 'pending_review'
            else:
                # Invalidates any in-flight worker via state + attempt identity.
                version.state, version.error = 'unchanged', None
                version.snapshot = VersionSnapshot().model_dump(mode='json')
        await db.commit()
        return version_response(version)


@router.post('/{kind}/{reference}/disable', response_model=VersionHistoryResponse)
async def disable(kind: SourceKind, reference: UUID, data: VersionActorRequest,
                  project_id: UUID = Query(...), db: AsyncSession = Depends(get_db_session_dependency)) -> VersionHistoryResponse:
    async with errors():
        source, _ = await ensure_source(db, project_id, kind, reference, data.actor)
        column = KnowledgeGovernanceRecord.qa_pair_id if kind == 'qa' else KnowledgeGovernanceRecord.file_id
        identifier = reference if kind == 'qa' else source.active_file_id
        record = (await db.execute(select(KnowledgeGovernanceRecord).where(
            KnowledgeGovernanceRecord.project_id == project_id, column == identifier,
            KnowledgeGovernanceRecord.deleted_at.is_(None),
        ).with_for_update())).scalar_one_or_none()
        if record:
            record.allow_automatic_reply = False
        pending = (await db.execute(select(KnowledgeVersion).where(
            KnowledgeVersion.source_id == source.id,
            KnowledgeVersion.state.in_(['processing', 'ready', 'pending_review']),
        ))).scalars().all()
        for version in pending:
            version.state, version.error = 'failed', '资料已停用，本次更新已取消。'
        source.disabled = True
        await db.commit()
        return await history(db, project_id, kind, reference)


@router.post('/{kind}/{reference}/enable', response_model=VersionHistoryResponse)
async def enable(kind: SourceKind, reference: UUID, data: VersionActorRequest,
                 project_id: UUID = Query(...), db: AsyncSession = Depends(get_db_session_dependency)) -> VersionHistoryResponse:
    async with errors():
        source, _ = await ensure_source(db, project_id, kind, reference, data.actor)
        column = KnowledgeGovernanceRecord.qa_pair_id if kind == 'qa' else KnowledgeGovernanceRecord.file_id
        record = (await db.execute(select(KnowledgeGovernanceRecord).where(
            KnowledgeGovernanceRecord.project_id == project_id,
            column == (reference if kind == 'qa' else source.active_file_id),
            KnowledgeGovernanceRecord.deleted_at.is_(None),
        ).with_for_update())).scalar_one_or_none()
        now = datetime.now(UTC)
        if (not record or record.review_status != 'approved' or record.source_origin == 'customer'
                or record.effective_at > now or (record.expires_at and record.expires_at <= now)):
            raise VersionConflict('当前资料未审核通过或不在有效期内，请先处理审核或有效期。')
        record.allow_automatic_reply, source.disabled = True, False
        await db.commit()
        return await history(db, project_id, kind, reference)


@router.post('/{kind}/{reference}/cleanup', response_model=VersionMaintenanceResponse)
async def cleanup(kind: SourceKind, reference: UUID, data: VersionMaintenanceRequest,
                  project_id: UUID = Query(...), db: AsyncSession = Depends(get_db_session_dependency)) -> VersionMaintenanceResponse:
    async with errors():
        source, _ = await ensure_source(db, project_id, kind, reference, 'system')
        from ..services.knowledge_version_storage import detach_unused_blobs, remove_blobs
        preview = await cleanup_history(db, source, data.retention, False)
        if data.confirm and sorted(preview.removable_numbers) != sorted(data.expected_numbers or []):
            raise VersionConflict('历史版本已变化，请重新预览并确认要清理的版本。')
        candidates = (await db.execute(select(KnowledgeVersion.snapshot).where(
            KnowledgeVersion.source_id == source.id,
            KnowledgeVersion.number.in_(preview.removable_numbers),
        ))).scalars().all()
        paths = {str(snapshot['storage_path']) for snapshot in candidates if snapshot.get('storage_path')}
        result = await cleanup_history(db, source, data.retention, data.confirm)
        removable = await detach_unused_blobs(db, source, paths) if data.confirm else []
        await db.commit()
        result.file_cleanup_pending = remove_blobs(removable)
        return result


@router.post('/file/{reference}/upload')
async def upload_replacement(reference: UUID, project_id: UUID = Query(...),
                             actor: str = Form(...), file: UploadFile = Upload(...),
                             db: AsyncSession = Depends(get_db_session_dependency)) -> dict[str, UUID]:
    settings = get_settings()
    if file.content_type not in settings.allowed_file_types:
        raise HTTPException(415, '不支持此文件格式。')
    content = await file.read(settings.max_file_size + 1)
    if not content or len(content) > settings.max_file_size:
        raise HTTPException(413, '文件为空或超过大小限制。')
    async with errors():
        source, _ = await ensure_source(db, project_id, 'file', reference, actor)
        identifier = uuid4()
        suffix = Path(file.filename or '').suffix[:12]
        path = Path(settings.upload_dir) / f'{identifier}{suffix}'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        db.add(File(id=identifier, project_id=project_id, collection_id=None,
                    original_filename=Path(file.filename or '替换文件').name,
                    file_size=len(content), content_type=file.content_type,
                    storage_provider='local', storage_path=str(path), status='pending',
                    storage_metadata={'version_source': str(source.id)}))
        try:
            await db.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {'id': identifier}
