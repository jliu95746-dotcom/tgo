"""Opt-in PostgreSQL tests. Every fixture uses a new tenant and rolls back its transaction."""
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sqlalchemy import select
from src.rag_service.database import get_db_session
from src.rag_service.models import Collection, FileDocument, QAPair
from src.rag_service.models.knowledge_governance import KnowledgeGovernanceRecord
from src.rag_service.schemas.knowledge_versions import VersionChangeRequest, VersionChunk, VersionInput, VersionSnapshot
from src.rag_service.services.knowledge_versions import VersionConflict, VersionNotFound, create_change, history, snapshot_digest
from src.rag_service.services.knowledge_version_publish import cleanup_history, publish, restore

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('TGO_VERSION_DB_TEST') != '1', reason='Requires isolated PostgreSQL transactions')]


async def fixture(db):
    project = uuid4()
    collection = Collection(id=uuid4(), project_id=project, display_name='版本事务验收', collection_type='qa')
    db.add(collection)
    await db.flush()
    document = FileDocument(id=uuid4(), project_id=project, collection_id=collection.id,
                            content='问题: 保修多久？\n\n答案: 一年', content_length=20,
                            embedding=[0.1] * 1536, embedding_dimensions=1536, embedding_model='fixture', chunk_index=0)
    db.add(document)
    await db.flush()
    pair = QAPair(id=uuid4(), project_id=project, collection_id=collection.id, question='保修多久？',
                  answer='一年', question_hash=uuid4().hex, document_id=document.id, status='processed')
    db.add(pair)
    await db.flush()
    return project, pair


def prepared(version, answer):
    content = VersionInput(question='保修多久？', answer=answer)
    snapshot = VersionSnapshot(content=content, chunks=[VersionChunk(
        content=f'问题: 保修多久？\n\n答案: {answer}', embedding=[0.2] * 1536, embedding_model='fixture', content_type='qa_pair')])
    version.snapshot, version.digest, version.state = snapshot.model_dump(mode='json'), snapshot_digest(snapshot), 'ready'


async def test_draft_publish_restore_and_retention_preserve_active():
    async with get_db_session() as db:
        project, pair = await fixture(db)
        old_id = pair.document_id
        revision = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(
            actor='测试员工', content=VersionInput(answer='两年'), action='submit'))
        assert revision.number == 2
        assert pair.answer == '一年' and pair.document_id == old_id
        assert (await db.get(FileDocument, old_id)).content.endswith('一年')
        prepared(revision, '两年')
        await publish(db, project, revision.id, '测试管理员')
        assert pair.answer == '两年' and pair.document_id != old_id
        assert await db.get(FileDocument, old_id) is None
        gate = (await db.execute(select(KnowledgeGovernanceRecord).where(KnowledgeGovernanceRecord.qa_pair_id == pair.id))).scalar_one()
        assert gate.review_status == 'approved' and gate.allow_automatic_reply
        result = await history(db, project, 'qa', pair.id)
        baseline = next(row for row in result.versions if row.number == 1)
        restored = await restore(db, project, baseline.id, '测试管理员')
        assert restored.number == 3 and restored.restored_from == 1
        assert pair.answer == '一年'
        from src.rag_service.services.knowledge_versions import find_source
        source = await find_source(db, project, 'qa', pair.id)
        preview = await cleanup_history(db, source, 2, False)
        assert preview.removable_numbers == [1] and preview.removed == 0
        removed = await cleanup_history(db, source, 2, True)
        assert removed.removed == 1 and source.active_number == 3
        await db.rollback()


async def test_invalid_draft_cannot_remove_old_document_and_cross_tenant_rejected():
    async with get_db_session() as db:
        project, pair = await fixture(db)
        old_id = pair.document_id
        revision = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        revision.state = 'ready'
        with pytest.raises(ValueError):
            await publish(db, project, revision.id, '管理员')
        assert pair.document_id == old_id
        assert await db.get(FileDocument, old_id) is not None
        with pytest.raises(VersionNotFound):
            await publish(db, uuid4(), revision.id, '其他项目管理员')
        revision.state = 'processing'
        with pytest.raises(VersionConflict):
            await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        await db.rollback()


async def test_expired_governance_cannot_be_reapproved_by_update():
    async with get_db_session() as db:
        project, pair = await fixture(db)
        revision = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        prepared(revision, '两年')
        db.add(KnowledgeGovernanceRecord(project_id=project, qa_pair_id=pair.id,
            document_type='faq', product_line='验收', channels=['web'], effective_at=datetime(2020, 1, 1, tzinfo=UTC),
            expires_at=datetime(2021, 1, 1, tzinfo=UTC), owner='管理员', document_version='V1'))
        await db.flush()
        with pytest.raises(VersionConflict, match='有效期'):
            await publish(db, project, revision.id, '管理员')
        assert pair.answer == '一年'
        await db.rollback()


async def test_editing_draft_invalidates_stale_approval_id():
    async with get_db_session() as db:
        project, pair = await fixture(db)
        first = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        first_id, first_number = first.id, first.number
        prepared(first, '两年')
        second = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工', content=VersionInput(answer='三年')))
        assert second.id != first_id and second.number == first_number
        with pytest.raises(VersionNotFound):
            await publish(db, project, first_id, '管理员')
        assert pair.answer == '一年'
        await db.rollback()


async def test_failed_draft_before_restore_does_not_reuse_an_older_version_number():
    async with get_db_session() as db:
        project, pair = await fixture(db)
        second = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        prepared(second, '两年')
        await publish(db, project, second.id, '管理员')
        baseline = next(row for row in (await history(db, project, 'qa', pair.id)).versions if row.number == 1)
        failed = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        failed.state = 'failed'
        restored = await restore(db, project, baseline.id, '管理员')
        following = await create_change(db, project, 'qa', pair.id, VersionChangeRequest(actor='员工'))
        assert following.number > restored.number
        await db.rollback()


@pytest.mark.parametrize('kind', ['file', 'website'])
async def test_file_sources_keep_stable_identity_on_publish_and_restore(kind):
    from src.rag_service.models import File, WebsitePage
    from src.rag_service.services.knowledge_versions import ensure_source
    async with get_db_session() as db:
        project, cid, fid = uuid4(), uuid4(), uuid4()
        db.add(Collection(id=cid, project_id=project, display_name='文件版本事务验收', collection_type=kind))
        await db.flush()
        file = File(id=fid, project_id=project, collection_id=cid, original_filename='旧说明.md',
                    file_size=8, content_type='text/markdown', storage_provider='local', storage_path='fixture-old.md', status='completed')
        db.add(file)
        await db.flush()
        db.add(FileDocument(id=uuid4(), project_id=project, collection_id=cid, file_id=fid,
            content='旧说明', content_length=3, chunk_index=0, embedding=[0.1]*1536,
            embedding_model='fixture', embedding_dimensions=1536))
        reference = fid
        if kind == 'website':
            page = WebsitePage(id=uuid4(), project_id=project, collection_id=cid, file_id=fid,
                               url='https://example.invalid/fixture', url_hash=uuid4().hex, status='processed', content_markdown='旧说明')
            db.add(page)
            reference = page.id
        await db.flush()
        source, _ = await ensure_source(db, project, kind, reference, '员工')
        replacement = File(id=uuid4(), project_id=project, collection_id=None, original_filename='新说明.md',
            file_size=8, content_type='text/markdown', storage_provider='local', storage_path='fixture-new.md', status='pending',
            storage_metadata={'version_source': str(source.id)})
        db.add(replacement)
        await db.flush()
        revision = await create_change(db, project, kind, reference, VersionChangeRequest(actor='员工',
            content=VersionInput(replacement_file_id=replacement.id) if kind == 'file' else VersionInput()))
        snapshot = VersionSnapshot(filename='新说明.md', storage_path='fixture-new.md', file_size=8,
            content_type='text/markdown', website_markdown='新说明', website_title='新标题',
            chunks=[VersionChunk(content='新说明', embedding=[0.2]*1536, embedding_model='fixture')])
        revision.state, revision.snapshot, revision.digest = 'ready', snapshot.model_dump(mode='json'), snapshot_digest(snapshot)
        await publish(db, project, revision.id, '管理员')
        assert source.active_file_id == fid and file.storage_path == 'fixture-new.md'
        docs = (await db.execute(select(FileDocument).where(FileDocument.file_id == fid))).scalars().all()
        assert len(docs) == 1 and docs[0].content == '新说明'
        baseline = next(row for row in (await history(db, project, kind, reference)).versions if row.number == 1)
        await restore(db, project, baseline.id, '管理员')
        assert source.active_file_id == fid and file.storage_path == 'fixture-old.md'
        if kind == 'website': assert page.content_markdown == '旧说明'
        await db.rollback()
