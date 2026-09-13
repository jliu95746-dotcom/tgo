"""Atomic publication, rollback and explicit history cleanup."""
from datetime import UTC, datetime
from uuid import UUID, uuid4
from typing import cast

from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Collection, File, FileDocument, QAPair, WebsitePage
from ..models.knowledge_governance import KnowledgeGovernanceRecord
from ..models.knowledge_versions import KnowledgeVersion, KnowledgeVersionSource
from ..schemas.knowledge_governance import KnowledgeGovernanceInput
from ..schemas.knowledge_versions import VersionMaintenanceResponse, VersionSnapshot
from .knowledge_governance import KnowledgeGovernanceService
from .knowledge_versions import SourceParent, VersionConflict, VersionNotFound, owned_parent, validate_snapshot


async def locked_version(db: AsyncSession, project: UUID, version_id: UUID) -> tuple[KnowledgeVersion, KnowledgeVersionSource, SourceParent]:
    row = (await db.execute(select(KnowledgeVersion, KnowledgeVersionSource).join(
        KnowledgeVersionSource, KnowledgeVersionSource.id == KnowledgeVersion.source_id,
    ).where(KnowledgeVersion.id == version_id, KnowledgeVersionSource.project_id == project))).one_or_none()
    if row is None:
        raise VersionNotFound('版本不存在或无权访问。')
    version, source = cast(KnowledgeVersion, row[0]), cast(KnowledgeVersionSource, row[1])
    parent = await owned_parent(db, project, source.source_kind, source.source_id)
    await db.refresh(source, with_for_update=True)
    await db.refresh(version, with_for_update=True)
    return version, source, parent


async def publish(db: AsyncSession, project: UUID, version_id: UUID, actor: str) -> KnowledgeVersion:
    version, source, parent = await locked_version(db, project, version_id)
    if version.state == 'published' and source.active_number == version.number:
        return version
    if version.state not in {'ready', 'pending_review'}:
        raise VersionConflict('只有处理成功的草稿或待审核版本才能生效。')
    snapshot = VersionSnapshot.model_validate(version.snapshot)
    validate_snapshot(snapshot)
    if source.source_kind == 'qa' and len(snapshot.chunks) != 1:
        raise VersionConflict('问答版本必须包含一条完整答案。')
    if isinstance(parent, QAPair):
        from ..schemas.qa import compute_question_hash
        if not snapshot.content.question or not snapshot.content.answer:
            raise VersionConflict('问答内容不完整，未切换版本。')
        duplicate = (await db.execute(select(QAPair.id).where(
            QAPair.project_id == project, QAPair.collection_id == source.collection_id,
            QAPair.id != parent.id, QAPair.deleted_at.is_(None),
            QAPair.question_hash == compute_question_hash(snapshot.content.question),
        ))).first()
        if duplicate:
            raise VersionConflict('知识库中已存在相同问题，请合并或修改问题后再生效。')
    file = None
    if source.source_kind != 'qa':
        file = (await db.execute(select(File).where(
            File.id == source.active_file_id, File.project_id == project,
        ).with_for_update())).scalar_one_or_none()
        if file is None:
            if source.source_kind != 'website' or not snapshot.storage_path:
                raise VersionConflict('原文件不可用，未切换版本。')
            file = File(id=uuid4(), project_id=project, collection_id=source.collection_id,
                        original_filename=snapshot.filename, file_size=snapshot.file_size,
                        content_type=snapshot.content_type, storage_path=snapshot.storage_path,
                        storage_provider='local', status='completed',
                        storage_metadata={'source': 'website_crawl', 'page_id': str(parent.id)})
            db.add(file)
            source.active_file_id = file.id
            await db.flush()
    governance_column = (KnowledgeGovernanceRecord.qa_pair_id if source.source_kind == 'qa'
                         else KnowledgeGovernanceRecord.file_id)
    if source.source_kind == 'qa':
        governance_id = parent.id
    else:
        assert file is not None
        governance_id = file.id
    governance = (await db.execute(select(KnowledgeGovernanceRecord).where(
        KnowledgeGovernanceRecord.project_id == project, governance_column == governance_id,
        KnowledgeGovernanceRecord.deleted_at.is_(None),
    ).with_for_update())).scalar_one_or_none()
    now = datetime.now(UTC)
    if governance and governance.expires_at and governance.expires_at <= now:
        raise VersionConflict('资料有效期已结束，请先调整有效期再发布。')
    if governance and governance.source_origin == 'customer':
        raise VersionConflict('客户原始内容不能直接发布为自动回复知识。')
    if governance is None:
        collection = await db.get(Collection, source.collection_id)
        governance = KnowledgeGovernanceService.new_record(project, KnowledgeGovernanceInput(
            file_id=file.id if file else None,
            qa_pair_id=parent.id if source.source_kind == 'qa' else None,
            document_type='faq' if source.source_kind == 'qa' else 'product',
            product_line=collection.display_name[:128], channels=['web', 'wecom_kf'],
            effective_at=now, owner=actor, document_version=f'V{version.number}',
            source_origin='website' if source.source_kind == 'website' else 'internal',
        ))
        db.add(governance)
    governance.review_status, governance.reviewed_by, governance.reviewed_at = 'approved', actor, now
    governance.allow_automatic_reply = True
    governance.document_version = f'V{version.number}'
    governance.effective_at = now
    query = delete(FileDocument).where(
        FileDocument.project_id == project, FileDocument.collection_id == source.collection_id,
    )
    if isinstance(parent, QAPair):
        previous_id = parent.document_id
        parent.document_id = None
        await db.flush()
        query = query.where(FileDocument.id == previous_id)
    else:
        assert file is not None
        query = query.where(FileDocument.file_id == file.id)
    await db.execute(query)
    documents = []
    for index, chunk in enumerate(snapshot.chunks):
        document = FileDocument(
            id=uuid4(), project_id=project, collection_id=source.collection_id,
            file_id=file.id if file else None, content=chunk.content,
            document_title=chunk.title, content_length=len(chunk.content),
            token_count=chunk.token_count, chunk_index=index, content_type=chunk.content_type,
            embedding=chunk.embedding, embedding_dimensions=len(chunk.embedding),
            embedding_model=chunk.embedding_model,
            tags={'qa_pair_id': str(parent.id), 'source_type': 'qa'} if isinstance(parent, QAPair) else {},
        )
        db.add(document)
        documents.append(document)
    await db.flush()
    await db.execute(update(FileDocument).where(FileDocument.id.in_([row.id for row in documents])).values(
        content_tsv=func.to_tsvector(literal_column("'pg_catalog.english'::regconfig"), FileDocument.content),
    ))
    if isinstance(parent, QAPair):
        from ..schemas.qa import compute_question_hash
        if not snapshot.content.question or not snapshot.content.answer:
            raise VersionConflict('问答内容不完整，未切换版本。')
        parent.question, parent.answer = snapshot.content.question, snapshot.content.answer
        parent.question_hash = compute_question_hash(parent.question)
        parent.category, parent.subcategory = snapshot.content.category, snapshot.content.subcategory
        parent.tags, parent.priority = snapshot.content.tags, snapshot.content.priority or 0
        parent.document_id, parent.status, parent.error_message = documents[0].id, 'processed', None
    else:
        assert file is not None
        file.original_filename = snapshot.filename or file.original_filename
        file.storage_path = snapshot.storage_path or file.storage_path
        file.file_size = snapshot.file_size if snapshot.file_size is not None else file.file_size
        file.content_type = snapshot.content_type or file.content_type
        file.status, file.error_message, file.deleted_at = 'completed', None, None
        file.document_count, file.total_tokens = len(documents), sum(c.token_count for c in snapshot.chunks)
        if isinstance(parent, WebsitePage):
            parent.file_id, parent.status, parent.error_message = file.id, 'processed', None
            if snapshot.website_markdown is not None:
                from .crawler import content_hash
                parent.content_markdown = snapshot.website_markdown
                parent.content_length = len(snapshot.website_markdown)
                parent.content_hash = content_hash(snapshot.website_markdown)
                parent.title = snapshot.website_title
    await db.execute(update(KnowledgeVersion).where(
        KnowledgeVersion.source_id == source.id, KnowledgeVersion.state == 'published',
        KnowledgeVersion.id != version.id,
    ).values(state='retired'))
    version.state, version.published_by, version.published_at = 'published', actor, now
    source.active_number, source.disabled = version.number, False
    await db.flush()
    return version


async def restore(db: AsyncSession, project: UUID, version_id: UUID, actor: str) -> KnowledgeVersion:
    previous, source, _ = await locked_version(db, project, version_id)
    if previous.state != 'retired':
        raise VersionConflict('只能恢复保留完整内容的历史生效版本。')
    pending = (await db.execute(select(KnowledgeVersion.id).where(
        KnowledgeVersion.source_id == source.id,
        KnowledgeVersion.state.in_(['processing', 'ready', 'pending_review']),
    ))).first()
    if pending:
        raise VersionConflict('请先处理或放弃待更新版本，再恢复历史版本。')
    await db.execute(delete(KnowledgeVersion).where(
        KnowledgeVersion.source_id == source.id, KnowledgeVersion.state == 'unchanged',
    ))
    number = (await db.execute(select(func.max(KnowledgeVersion.number)).where(
        KnowledgeVersion.source_id == source.id,
    ))).scalar_one() + 1
    revision = KnowledgeVersion(
        id=uuid4(), source_id=source.id, number=number, state='ready', author=actor,
        requested_action='publish', snapshot=previous.snapshot.copy(), digest=previous.digest,
        restored_from=previous.number,
    )
    db.add(revision)
    await db.flush()
    return await publish(db, project, revision.id, actor)


async def cleanup_history(db: AsyncSession, source: KnowledgeVersionSource,
                          retention: int, confirm: bool) -> VersionMaintenanceResponse:
    versions = (await db.execute(select(KnowledgeVersion).where(
        KnowledgeVersion.source_id == source.id,
        KnowledgeVersion.state.in_(['published', 'retired']),
    ).order_by(KnowledgeVersion.number.desc()))).scalars().all()
    removable = [row for row in versions[retention:] if row.number != source.active_number and row.state == 'retired']
    if confirm:
        source.retention = retention
        for row in removable:
            await db.delete(row)
    return VersionMaintenanceResponse(removable_numbers=[row.number for row in removable],
                                      removed=len(removable) if confirm else 0)
