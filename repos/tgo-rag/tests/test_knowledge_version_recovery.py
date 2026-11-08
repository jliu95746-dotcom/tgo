"""Native database checks for revision ownership and attempt recovery."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
import re
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.rag_service.models import (
    Base,
    Collection,
    File,
    FileDocument,
    QAPair,
    WebsitePage,
)
from src.rag_service.models.knowledge_versions import (
    KnowledgeVersion,
    KnowledgeVersionSource,
)
from src.rag_service.schemas.knowledge_versions import (
    SourceKind,
    VersionAction,
    VersionChangeRequest,
    VersionInput,
    VersionSnapshot,
)
from src.rag_service.services import knowledge_version_prepare as preparation
from src.rag_service.services.knowledge_version_publish import locked_version
from src.rag_service.services.knowledge_versions import (
    VersionNotFound,
    create_change,
    ensure_source,
)
from src.rag_service.services.qa_documents import build_qa_content

pytestmark = pytest.mark.skipif(
    not os.getenv("TGO_VERSION_PREPARE_TEST_DATABASE_URL"),
    reason="Requires owned version preparation PostgreSQL database",
)


@pytest.fixture(scope="module")
def version_database():
    value = os.environ["TGO_VERSION_PREPARE_TEST_DATABASE_URL"]
    url = make_url(value)
    assert url.host == "127.0.0.1"
    assert url.database.startswith("tgo_version_prepare_test_")
    schema = "version_guard_" + uuid4().hex
    assert re.fullmatch(r"version_guard_[0-9a-f]{32}", schema)

    async def setup():
        engine = create_async_engine(
            value, poolclass=NullPool, hide_parameters=True
        )
        try:
            async with engine.begin() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_tables "
                            "WHERE schemaname='public'"
                        )
                    )
                    == 0
                )
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}", public')
                )
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    async def cleanup():
        engine = create_async_engine(
            value, poolclass=NullPool, hide_parameters=True
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA "{schema}" CASCADE')
                )
        finally:
            await engine.dispose()

    asyncio.run(setup())
    try:
        yield value, schema
    finally:
        asyncio.run(cleanup())


@dataclass
class VersionCase:
    project: UUID
    collection: UUID
    parent: UUID
    source: UUID
    revision: UUID
    attempt: UUID
    document: UUID
    kind: SourceKind


class VersionStore:
    def __init__(self, factory, root, provider, provider_factory):
        self.factory = factory
        self.root = root
        self.provider = provider
        self.provider_factory = provider_factory

    async def make(
        self, kind: SourceKind = "qa", action: VersionAction = "save"
    ):
        project = uuid4()
        async with self.factory() as db:
            collection = Collection(
                project_id=project, display_name="版本隔离测试", collection_type=kind
            )
            db.add(collection)
            await db.flush()
            file = None
            if kind != "qa":
                path = self.root / (uuid4().hex + ".md")
                path.write_text("原商品资料", encoding="utf-8")
                file = File(
                    project_id=project,
                    collection_id=collection.id,
                    original_filename="原资料.md",
                    file_size=20,
                    content_type="text/markdown",
                    storage_provider="local",
                    storage_path=str(path),
                    status="completed",
                )
                db.add(file)
                await db.flush()
            body = build_qa_content("保修多久？", "一年") if kind == "qa" else "原商品资料"
            document = FileDocument(
                project_id=project,
                collection_id=collection.id,
                file_id=file.id if file else None,
                content=body,
                content_length=len(body),
                chunk_index=0,
                embedding=[0.25] * 1536,
                embedding_dimensions=1536,
                embedding_model="fixture",
            )
            db.add(document)
            await db.flush()
            if kind == "qa":
                parent = QAPair(
                    project_id=project,
                    collection_id=collection.id,
                    question="保修多久？",
                    answer="一年",
                    question_hash=uuid4().hex,
                    document_id=document.id,
                    status="processed",
                )
            elif kind == "website":
                parent = WebsitePage(
                    project_id=project,
                    collection_id=collection.id,
                    file_id=file.id,
                    url="https://example.invalid/policy",
                    url_hash=uuid4().hex,
                    status="processed",
                    content_markdown=body,
                )
            else:
                parent = file
            db.add(parent)
            await db.flush()
            source, _ = await ensure_source(
                db, project, kind, parent.id, "测试员工"
            )
            content = (
                VersionInput(question="保修多久？", answer="两年")
                if kind == "qa"
                else VersionInput()
            )
            if kind == "file":
                path = self.root / (uuid4().hex + ".md")
                path.write_text("新版商品资料", encoding="utf-8")
                replacement = File(
                    project_id=project,
                    collection_id=None,
                    original_filename="新版资料.md",
                    file_size=30,
                    content_type="text/markdown",
                    storage_provider="local",
                    storage_path=str(path),
                    status="pending",
                    storage_metadata={"version_source": str(source.id)},
                )
                db.add(replacement)
                await db.flush()
                content = VersionInput(replacement_file_id=replacement.id)
            revision = await create_change(
                db,
                project,
                kind,
                parent.id,
                VersionChangeRequest(
                    actor="测试员工", content=content, action=action
                ),
            )
            attempt = VersionSnapshot.model_validate(revision.snapshot).attempt
            assert attempt is not None
            await db.commit()
            return VersionCase(
                project,
                collection.id,
                parent.id,
                source.id,
                revision.id,
                attempt,
                document.id,
                kind,
            )

    async def read(self, case):
        async with self.factory() as db:
            revision = await db.get(KnowledgeVersion, case.revision)
            source = await db.get(KnowledgeVersionSource, case.source)
            documents = (
                (
                    await db.execute(
                        select(FileDocument).where(
                            FileDocument.collection_id == case.collection,
                            FileDocument.project_id == case.project,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return revision, source, documents


@pytest_asyncio.fixture
async def version_store(version_database, tmp_path, monkeypatch):
    value, schema = version_database
    engine = create_async_engine(
        value,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "server_settings": {"search_path": f'"{schema}",public'}
        },
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def sessions():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    provider = MagicMock()
    provider.generate_embeddings_batch = AsyncMock(
        side_effect=lambda contents: [[0.5] * 1536 for _ in contents]
    )
    provider.get_embedding_model.return_value = "fixture"
    provider_factory = AsyncMock(return_value=provider)
    monkeypatch.setattr(preparation, "get_db_session", sessions)
    monkeypatch.setattr(
        preparation, "get_embedding_service_for_project", provider_factory
    )
    try:
        yield VersionStore(factory, tmp_path, provider, provider_factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failure_recovery_cannot_modify_another_project(version_store):
    target = await version_store.make()
    caller = await version_store.make()
    result = await preparation.process_revision(
        caller.project, target.revision, target.attempt
    )
    assert result == "failed"
    revision, source, documents = await version_store.read(target)
    assert revision.state == "processing" and revision.error is None
    assert source.active_number == 1
    assert [row.id for row in documents] == [target.document]
    assert (await version_store.read(caller))[0].state == "processing"
    version_store.provider_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_provider_failure_preserves_live_knowledge(version_store):
    case = await version_store.make()
    private = "api_key=synthetic-private-fixture"
    version_store.provider.generate_embeddings_batch.side_effect = (
        RuntimeError(private)
    )
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == "failed" and private not in revision.error
    assert source.active_number == 1 and [row.id for row in documents] == [
        case.document
    ]
    assert documents[0].content == build_qa_content("保修多久？", "一年")


@pytest.mark.asyncio
async def test_stale_attempt_does_not_overwrite_a_current_job(version_store):
    case = await version_store.make()
    assert (
        await preparation.process_revision(
            case.project, case.revision, uuid4()
        )
        == "failed"
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == "processing" and revision.error is None
    assert source.active_number == 1 and documents[0].id == case.document
    version_store.provider_factory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["ready", "pending_review", "retired", "failed", "unchanged"]
)
async def test_non_processing_versions_are_not_changed(version_store, state):
    case = await version_store.make()
    async with version_store.factory() as db:
        revision = await db.get(KnowledgeVersion, case.revision)
        revision.state, revision.error = state, "原状态标记"
        await db.commit()
    await preparation.process_revision(
        case.project, case.revision, case.attempt
    )
    revision, _, _ = await version_store.read(case)
    assert revision.state == state and revision.error == "原状态标记"
    version_store.provider_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_bad_payload_can_fail_without_parsing_it_twice(
    version_store,
):
    case = await version_store.make()
    async with version_store.factory() as db:
        revision = await db.get(KnowledgeVersion, case.revision)
        revision.snapshot = {
            **revision.snapshot,
            "chunks": "invalid-private-fixture",
        }
        await db.commit()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    revision, source, documents = await version_store.read(case)
    assert (
        revision.state == "failed"
        and "invalid-private-fixture" not in revision.error
    )
    assert source.active_number == 1 and documents[0].id == case.document


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", ["not-a-uuid", 123, None])
async def test_unknown_attempt_does_not_authorize_a_failure_write(
    version_store, nonce
):
    case = await version_store.make()
    async with version_store.factory() as db:
        revision = await db.get(KnowledgeVersion, case.revision)
        revision.snapshot = {**revision.snapshot, "attempt": nonce}
        await db.commit()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == "processing" and revision.error is None
    assert source.active_number == 1 and documents[0].id == case.document


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "qa", "website"])
async def test_source_collection_must_match_the_owned_parent(
    version_store, kind
):
    case = await version_store.make(kind)
    async with version_store.factory() as db:
        other = Collection(
            project_id=case.project, display_name="另一知识库", collection_type=kind
        )
        db.add(other)
        await db.flush()
        source = await db.get(KnowledgeVersionSource, case.source)
        source.collection_id = other.id
        await db.commit()
    async with version_store.factory() as db:
        with pytest.raises(VersionNotFound):
            await locked_version(db, case.project, case.revision)


@pytest.mark.asyncio
async def test_deleted_parent_still_allows_owned_failure_recording(
    version_store,
):
    case = await version_store.make()
    async with version_store.factory() as db:
        parent = await db.get(QAPair, case.parent)
        parent.soft_delete()
        await db.commit()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == "failed" and source.active_number == 1
    assert documents[0].id == case.document


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,expected",
    [
        ("save", "ready"),
        ("submit", "pending_review"),
        ("publish", "published"),
    ],
)
async def test_qa_update_completes_the_requested_transition(
    version_store, action, expected
):
    case = await version_store.make(action=action)
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == expected
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == expected
    if action == "publish":
        assert source.active_number == 2 and documents[0].id != case.document
        assert documents[0].content == build_qa_content("保修多久？", "两年")
    else:
        assert source.active_number == 1 and documents[0].id == case.document
    version_store.provider.generate_embeddings_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unchanged_content_does_not_call_an_embedding_provider(
    version_store,
):
    case = await version_store.make()
    async with version_store.factory() as db:
        revision = await db.get(KnowledgeVersion, case.revision)
        snapshot = VersionSnapshot.model_validate(revision.snapshot)
        snapshot.content.answer = "一年"
        revision.snapshot = snapshot.model_dump(mode="json")
        await db.commit()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "unchanged"
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == "unchanged" and source.active_number == 1
    assert documents[0].id == case.document
    version_store.provider_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_update_keeps_stable_identity_and_publishes_new_text(
    version_store, monkeypatch
):
    case = await version_store.make("file", "publish")
    from src.rag_service.tasks import document_chunking

    def chunks(documents, *_args):
        return [{"content": item.page_content} for item in documents]

    monkeypatch.setattr(document_chunking, "chunk_documents", chunks)
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "published"
    )
    revision, source, documents = await version_store.read(case)
    assert revision.state == "published" and source.active_number == 2
    assert source.active_file_id == case.parent
    assert [row.content for row in documents] == ["新版商品资料"]
