"""
Actual PostgreSQL/HTTP checks using a rollback-only, isolated test schema.
"""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.rag_service.database import get_db_session_dependency
from src.rag_service.models import (
    Collection,
    CollectionType,
    File,
    FileDocument,
    KnowledgeGovernanceRecord,
    KnowledgeVersion,
    KnowledgeVersionSource,
    QAPair,
    WebsitePage,
)
from src.rag_service.routers.knowledge_governance import router
from src.rag_service.schemas.knowledge_governance import KnowledgeChannel
from src.rag_service.services.knowledge_revision import (
    collection_revision_statement,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in isolated PostgreSQL test",
)
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def revision_state():
    url = make_url(os.environ["SAAS_TEST_DATABASE_URL"])
    assert url.host in {"127.0.0.1", "localhost"}
    assert url.database.startswith("tgo_scenarios_test_")
    engine = create_async_engine(url, hide_parameters=True)
    schema = "revision_" + uuid4().hex
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}", public')
                )
                for model in (
                    Collection,
                    File,
                    FileDocument,
                    QAPair,
                    WebsitePage,
                    KnowledgeGovernanceRecord,
                    KnowledgeVersionSource,
                    KnowledgeVersion,
                ):
                    await connection.run_sync(model.__table__.create)
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    project, foreign = uuid4(), uuid4()
                    collection = Collection(
                        project_id=project, display_name="业务知识"
                    )
                    other = Collection(
                        project_id=foreign, display_name="另一企业私有资料"
                    )
                    db.add_all([collection, other])
                    await db.flush()
                    file = File(
                        project_id=project,
                        collection_id=collection.id,
                        original_filename="商品说明",
                        file_size=50,
                        content_type="text/plain",
                        storage_provider="local",
                        storage_path="synthetic-not-a-real-path",
                        status="completed",
                    )
                    db.add(file)
                    await db.flush()
                    document = FileDocument(
                        project_id=project,
                        collection_id=collection.id,
                        file_id=file.id,
                        content="澜月：普通皮革，三天发货。",
                    )
                    record = KnowledgeGovernanceRecord(
                        project_id=project,
                        file_id=file.id,
                        document_type="product",
                        product_line="澜月",
                        channels=["web", "wecom_kf"],
                        effective_at=AT - timedelta(days=1),
                        expires_at=AT + timedelta(days=1),
                        owner="测试负责人",
                        document_version="v1",
                        allow_automatic_reply=True,
                        review_status="approved",
                        reviewed_by="测试审核人",
                        reviewed_at=AT,
                        source_origin="internal",
                    )
                    version_source = KnowledgeVersionSource(
                        project_id=project,
                        collection_id=collection.id,
                        source_kind="file",
                        source_id=file.id,
                        active_file_id=file.id,
                        active_number=1,
                    )
                    db.add_all([document, record, version_source])
                    await db.flush()
                    published = KnowledgeVersion(
                        source_id=version_source.id,
                        number=1,
                        state="published",
                        author="测试",
                        digest="a" * 64,
                        snapshot={"private": "not returned"},
                    )
                    db.add(published)
                    await db.flush()

                    async def database():
                        yield db

                    app = FastAPI()
                    app.include_router(
                        router, prefix="/v1/knowledge-governance"
                    )
                    app.dependency_overrides[
                        get_db_session_dependency
                    ] = database
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://test",
                    ) as client:
                        yield SimpleNamespace(
                            db=db,
                            client=client,
                            project=project,
                            foreign=foreign,
                            collection=collection,
                            other=other,
                            file=file,
                            document=document,
                            record=record,
                            source=version_source,
                            published=published,
                        )
            finally:
                await outer.rollback()
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.schemata"
                        " WHERE schema_name = :schema"
                    ),
                    {"schema": schema},
                )
                == 0
            )
    finally:
        await engine.dispose()


async def snapshot(state, *, at=AT, channel="web"):
    await state.db.flush()
    rows = (
        await state.db.execute(
            collection_revision_statement(
                state.project, KnowledgeChannel(channel), at
            ),
        )
    ).all()
    assert len(rows) == 1
    # (collection id, label, total chunks, eligible chunks, resource revision)
    return rows[0]


async def governed_website(state):
    """
    Website material retains review gates after file authoring simplification.
    """
    state.collection.collection_type = CollectionType.website
    page = WebsitePage(
        id=uuid4(),
        project_id=state.project,
        collection_id=state.collection.id,
        file_id=state.file.id,
        url="https://fixture.invalid/knowledge",
        url_hash=uuid4().hex * 2,
        status="processed",
    )
    state.db.add(page)
    state.source.source_kind = "website"
    state.source.source_id = page.id
    await state.db.flush()


@pytest.mark.asyncio
async def test_same_content_repeated_reads_and_rename_preserve_revision(
    revision_state,
):
    state = revision_state
    first = await snapshot(state)
    assert first[2:4] == (1, 1)
    assert len(first[4]) == 64
    assert (await snapshot(state, at=AT + timedelta(hours=1)))[4] == first[4]
    state.collection.display_name = "新展示名"
    assert (await snapshot(state))[4] == first[4]
    assert (await snapshot(state, channel="wecom_kf"))[4] != first[4]


@pytest.mark.asyncio
async def test_content_edit_changes_revision_with_stable_count_and_time(
    revision_state,
):
    state = revision_state
    first = await snapshot(state)
    previous_updated = state.document.updated_at
    state.document.content = "澜月：牛皮，五天发货。"
    state.document.updated_at = previous_updated
    second = await snapshot(state)
    assert second[2:4] == first[2:4]
    assert second[4] != first[4]


@pytest.mark.asyncio
async def test_search_index_edit_without_content_or_timestamp_changes_revision(
    revision_state,
):
    state = revision_state
    first = await snapshot(state)
    previous_updated = state.document.updated_at
    state.document.content_tsv = "'service':1"
    state.document.updated_at = previous_updated
    second = await snapshot(state)
    assert second[2:4] == first[2:4] and second[4] != first[4]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["revoked", "channel", "disabled", "future", "deleted"]
)
async def test_governance_changes_invalidate_with_unchanged_total(
    revision_state, change
):
    state = revision_state
    await governed_website(state)
    first = await snapshot(state)
    if change == "revoked":
        state.record.review_status = "revoked"
    elif change == "channel":
        state.record.channels = ["internal"]
    elif change == "disabled":
        state.record.allow_automatic_reply = False
    elif change == "future":
        state.record.effective_at = AT + timedelta(hours=1)
    else:
        state.record.deleted_at = AT
    second = await snapshot(state)
    assert second[2:4] == (1, 0)
    assert second[4] != first[4]


@pytest.mark.asyncio
async def test_effective_and_expiry_boundaries_change_without_writes(
    revision_state,
):
    state = revision_state
    await governed_website(state)
    state.record.effective_at = AT + timedelta(hours=1)
    before = await snapshot(state)
    effective = await snapshot(state, at=state.record.effective_at)
    assert before[3] == 0 and effective[3] == 1
    assert before[4] != effective[4]
    expired = await snapshot(state, at=state.record.expires_at)
    assert expired[3] == 0
    assert expired[4] != effective[4]
    assert (
        await snapshot(state, at=state.record.expires_at + timedelta(hours=1))
    )[4] == expired[4]


@pytest.mark.asyncio
async def test_active_source_changes_revision_but_pending_draft_does_not(
    revision_state,
):
    state = revision_state
    first = await snapshot(state)
    state.db.add(
        KnowledgeVersion(
            source_id=state.source.id,
            number=2,
            state="ready",
            author="测试",
            digest="b" * 64,
            snapshot={"private": "new pending draft"},
        )
    )
    assert (await snapshot(state))[4] == first[4]
    state.source.active_number = 2
    changed = await snapshot(state)
    assert changed[4] != first[4]
    state.source.disabled = True
    disabled = await snapshot(state)
    assert disabled[4] != changed[4]
    state.file.status = "processing"
    assert (await snapshot(state))[4] != disabled[4]


@pytest.mark.asyncio
async def test_http_revision_is_secret_free_and_tenant_scoped(revision_state):
    state = revision_state
    response = await state.client.get(
        "/v1/knowledge-governance/availability",
        params={
            "project_id": str(state.project),
            "channel": "web",
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()["collections"][0]
    assert row["id"] == str(state.collection.id)
    assert len(row["resource_revision"]) == 64
    for private in (
        state.document.content,
        state.file.storage_path,
        "not returned",
        str(state.foreign),
        str(state.other.id),
    ):
        assert private not in response.text
    empty = await state.client.get(
        "/v1/knowledge-governance/availability",
        params={
            "project_id": str(uuid4()),
            "channel": "web",
        },
    )
    assert empty.json()["collections"] == []


@pytest.mark.asyncio
async def test_qa_content_without_a_processed_chunk_is_also_versioned(
    revision_state,
):
    state = revision_state
    first = await snapshot(state)
    pair = QAPair(
        project_id=state.project,
        collection_id=state.collection.id,
        question="保修多久",
        answer="一年",
        question_hash="c" * 64,
        status="pending",
    )
    state.db.add(pair)
    added = await snapshot(state)
    assert added[2:4] == first[2:4] and added[4] != first[4]
    pair.answer = "两年"
    edited = await snapshot(state)
    assert edited[2:4] == added[2:4] and edited[4] != added[4]
    pair.deleted_at = AT
    assert (await snapshot(state))[4] == first[4]


@pytest.mark.asyncio
async def test_foreign_content_and_deleted_sources_respect_scope(
    revision_state,
):
    state = revision_state
    first = await snapshot(state)
    # Even corrupt metadata pointing at our collection is filtered by owner.
    state.db.add(
        FileDocument(
            project_id=state.foreign,
            collection_id=state.collection.id,
            content="另一企业私有内容",
            file_id=state.file.id,
        )
    )
    assert (await snapshot(state))[4] == first[4]
    state.file.deleted_at = AT
    removed = await snapshot(state)
    assert removed[2:4] == (1, 0) and removed[4] != first[4]
    state.collection.deleted_at = AT
    await state.db.flush()
    assert (
        await state.db.execute(
            collection_revision_statement(state.project, "web", AT)
        )
    ).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["revoked", "channel", "disabled", "future", "deleted"]
)
async def test_ordinary_files_remain_ready_without_legacy_controls(
    revision_state, change
):
    state = revision_state
    first = await snapshot(state)
    assert first[2:4] == (1, 1)
    if change == "revoked":
        state.record.review_status = "revoked"
    elif change == "channel":
        state.record.channels = ["internal"]
    elif change == "disabled":
        state.record.allow_automatic_reply = False
    elif change == "future":
        state.record.effective_at = AT + timedelta(hours=1)
    else:
        state.record.deleted_at = AT
    second = await snapshot(state)
    assert second[2:4] == (1, 1)
    assert second[4] != first[4]
    assert (await snapshot(state, channel="wecom_kf"))[2:4] == (1, 1)


@pytest.mark.asyncio
async def test_ordinary_files_do_not_expire_with_unused_governance_controls(
    revision_state,
):
    state = revision_state
    state.record.effective_at = AT + timedelta(hours=1)
    state.record.expires_at = AT + timedelta(hours=2)
    for instant in (AT, state.record.effective_at, state.record.expires_at):
        assert (await snapshot(state, at=instant))[2:4] == (1, 1)


@pytest.mark.asyncio
async def test_customer_material_is_not_admitted_as_an_ordinary_file(
    revision_state,
):
    state = revision_state
    assert (await snapshot(state))[2:4] == (1, 1)
    state.record.source_origin = "customer"
    assert (await snapshot(state))[2:4] == (1, 0)
