"""Version drafts are stored off-index; only publication touches live documents."""
import hashlib
import json
import math
from datetime import UTC, datetime
from uuid import UUID, uuid4
from typing import TypeAlias, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Collection, File, FileDocument, QAPair, WebsitePage
from ..models.knowledge_versions import KnowledgeVersion, KnowledgeVersionSource
from ..schemas.knowledge_versions import (
    SourceKind, VersionChangeRequest, VersionChunk, VersionHistoryResponse,
    VersionInput, VersionResponse, VersionSnapshot,
)


class VersionConflict(ValueError):
    """The requested transition would discard or publish an unsafe draft."""


class VersionNotFound(LookupError):
    """Source or version is absent from this tenant's live collection."""


SourceParent: TypeAlias = File | QAPair | WebsitePage


def snapshot_digest(snapshot: VersionSnapshot) -> str:
    value = {
        'content': snapshot.content.model_dump(mode='json', exclude={'replacement_file_id'}),
        'chunks': [chunk.content.strip() for chunk in snapshot.chunks],
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_snapshot(snapshot: VersionSnapshot) -> None:
    if not snapshot.chunks:
        raise ValueError('新版没有可用正文，原生效版本未改变。')
    for chunk in snapshot.chunks:
        if (not chunk.content.strip() or len(chunk.embedding) != 1536
                or not all(math.isfinite(value) for value in chunk.embedding)
                or not any(chunk.embedding)):
            raise ValueError('新版正文或向量无效，原生效版本未改变。')


async def owned_parent(db: AsyncSession, project: UUID, kind: SourceKind, reference: UUID) -> SourceParent:
    model = {'file': File, 'qa': QAPair, 'website': WebsitePage}[kind]
    statement = select(model).join(Collection, Collection.id == model.collection_id).where(
        model.id == reference, model.project_id == project,
        Collection.project_id == project, Collection.deleted_at.is_(None),
        Collection.collection_type == kind,
    )
    if kind != 'website':
        statement = statement.where(model.deleted_at.is_(None))
    parent = (await db.execute(statement.with_for_update(of=model))).scalar_one_or_none()
    if parent is None:
        raise VersionNotFound('资料不存在或无权访问。')
    return cast(SourceParent, parent)


async def find_source(db: AsyncSession, project: UUID, kind: SourceKind, reference: UUID) -> KnowledgeVersionSource | None:
    result = (await db.execute(select(KnowledgeVersionSource).where(
        KnowledgeVersionSource.project_id == project,
        KnowledgeVersionSource.source_kind == kind,
        KnowledgeVersionSource.source_id == reference,
    ).with_for_update())).scalar_one_or_none()
    return cast(KnowledgeVersionSource | None, result)


async def capture_live(db: AsyncSession, source: KnowledgeVersionSource, parent: SourceParent) -> VersionSnapshot:
    query = select(FileDocument).where(
        FileDocument.project_id == source.project_id,
        FileDocument.collection_id == source.collection_id,
    )
    if isinstance(parent, QAPair):
        query = query.where(FileDocument.id == parent.document_id)
        content = VersionInput(question=parent.question, answer=parent.answer,
                               category=parent.category, subcategory=parent.subcategory,
                               tags=parent.tags, priority=parent.priority)
        snapshot = VersionSnapshot(content=content)
    else:
        query = query.where(FileDocument.file_id == source.active_file_id)
        file = (await db.execute(select(File).where(
            File.id == source.active_file_id, File.project_id == source.project_id,
            File.deleted_at.is_(None),
        ))).scalar_one_or_none()
        snapshot = VersionSnapshot()
        if file:
            snapshot.filename, snapshot.storage_path = file.original_filename, file.storage_path
            snapshot.file_size, snapshot.content_type = file.file_size, file.content_type
        if isinstance(parent, WebsitePage):
            snapshot.website_markdown, snapshot.website_title = parent.content_markdown, parent.title
    documents = (await db.execute(query.order_by(FileDocument.chunk_index))).scalars().all()
    snapshot.chunks = [VersionChunk(
        content=row.content, title=row.document_title, token_count=row.token_count or 0,
        content_type=row.content_type or 'paragraph',
        embedding=list(row.embedding) if row.embedding is not None else [],
        embedding_model=row.embedding_model or '',
    ) for row in documents]
    return snapshot


async def ensure_source(db: AsyncSession, project: UUID, kind: SourceKind,
                        reference: UUID, actor: str) -> tuple[KnowledgeVersionSource, SourceParent]:
    parent = await owned_parent(db, project, kind, reference)
    running = {'pending', 'processing', 'crawling', 'fetched', 'extracted'}
    if parent.status in running:
        raise VersionConflict('原资料仍在处理中，请完成后再更新。')
    source = await find_source(db, project, kind, reference)
    if source:
        return source, parent
    source = KnowledgeVersionSource(
        project_id=project, collection_id=parent.collection_id,
        source_kind=kind, source_id=reference,
        active_file_id=parent.id if kind == 'file' else (parent.file_id if kind == 'website' else None),
        disabled=False, retention=10,
    )
    db.add(source)
    await db.flush()
    snapshot = await capture_live(db, source, parent)
    if snapshot.chunks:
        source.active_number = 1
        db.add(KnowledgeVersion(
            source_id=source.id, number=1, state='published', author='原有资料',
            requested_action='save', digest=snapshot_digest(snapshot),
            snapshot=snapshot.model_dump(mode='json'),
        ))
        await db.flush()
    return source, parent


async def create_change(db: AsyncSession, project: UUID, kind: SourceKind,
                        reference: UUID, request: VersionChangeRequest) -> KnowledgeVersion:
    source, parent = await ensure_source(db, project, kind, reference, request.actor)
    pending = (await db.execute(select(KnowledgeVersion).where(
        KnowledgeVersion.source_id == source.id,
        KnowledgeVersion.state.in_(['processing', 'ready', 'pending_review', 'failed', 'unchanged']),
    ).order_by(KnowledgeVersion.number.desc()))).scalars().first()
    if pending and pending.state == 'processing':
        raise VersionConflict('正在处理上一份修改，请稍后再试。')
    content = request.content
    if isinstance(parent, QAPair):
        content = VersionInput(
            question=content.question if content.question is not None else parent.question,
            answer=content.answer if content.answer is not None else parent.answer,
            category=content.category if 'category' in content.model_fields_set else parent.category,
            subcategory=content.subcategory if 'subcategory' in content.model_fields_set else parent.subcategory,
            tags=content.tags if 'tags' in content.model_fields_set else parent.tags,
            priority=content.priority if content.priority is not None else parent.priority,
        )
        if not content.question or not content.answer or not content.question.strip() or not content.answer.strip():
            raise VersionConflict('问题和答案不能为空。')
    if kind == 'file':
        replacement = (await db.execute(select(File).where(
            File.id == content.replacement_file_id, File.project_id == project,
            File.collection_id.is_(None), File.deleted_at.is_(None),
        ))).scalar_one_or_none()
        if not replacement or (replacement.storage_metadata or {}).get('version_source') != str(source.id):
            raise VersionConflict('请先上传属于这份资料的替换文件。')
    if pending is None or pending.number <= (source.active_number or 0):
        number = (await db.execute(select(func.coalesce(func.max(KnowledgeVersion.number), 0)).where(
            KnowledgeVersion.source_id == source.id,
        ))).scalar_one() + 1
    else:
        number = pending.number
    if pending is not None:
        # A stale review page must never approve newly edited content using an old revision ID.
        await db.delete(pending)
        await db.flush()
    pending = KnowledgeVersion(source_id=source.id, number=number)
    db.add(pending)
    pending.state, pending.author, pending.requested_action = 'processing', request.actor, request.action
    pending.error, pending.digest = None, None
    pending.created_at = datetime.now(UTC)
    pending.snapshot = VersionSnapshot(content=content, attempt=uuid4()).model_dump(mode='json')
    await db.flush()
    return pending


def version_response(version: KnowledgeVersion) -> VersionResponse:
    snapshot = VersionSnapshot.model_validate(version.snapshot)
    return VersionResponse(
        id=version.id, number=version.number, state=version.state, author=version.author,
        published_by=version.published_by, published_at=version.published_at,
        created_at=version.created_at, error=version.error, restored_from=version.restored_from,
        content=snapshot.content, preview='\n\n'.join(chunk.content for chunk in snapshot.chunks)[:8000],
    )


async def history(db: AsyncSession, project: UUID, kind: SourceKind,
                  reference: UUID) -> VersionHistoryResponse:
    await owned_parent(db, project, kind, reference)
    source = await find_source(db, project, kind, reference)
    versions = [] if source is None else (await db.execute(select(KnowledgeVersion).where(
        KnowledgeVersion.source_id == source.id,
    ).order_by(KnowledgeVersion.number.desc()))).scalars().all()
    return VersionHistoryResponse(
        source_kind=kind, source_id=reference,
        active_number=source.active_number if source else None,
        disabled=source.disabled if source else False, retention=source.retention if source else 10,
        versions=[version_response(version) for version in versions],
    )
