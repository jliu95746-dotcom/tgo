"""Website revision files follow the owned, committed version lifecycle."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.rag_service.config import get_settings
from src.rag_service.models.knowledge_versions import KnowledgeVersion
from src.rag_service.schemas.knowledge_versions import VersionSnapshot
from src.rag_service.services import knowledge_version_prepare as preparation
from src.rag_service.services.knowledge_versions import VersionConflict
from tests import test_knowledge_version_recovery as recovery

version_database = recovery.version_database
version_store = recovery.version_store
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TGO_VERSION_PREPARE_TEST_DATABASE_URL"),
        reason="Requires owned version preparation PostgreSQL database",
    ),
]


@pytest_asyncio.fixture
async def website_store(version_store, monkeypatch):
    from src.rag_service.services.crawler import WebCrawlerService
    from src.rag_service.tasks import document_chunking

    monkeypatch.setattr(get_settings(), "upload_dir", str(version_store.root))
    crawl = AsyncMock(
        return_value=SimpleNamespace(content_markdown="新版网站资料", title="网站政策")
    )
    monkeypatch.setattr(WebCrawlerService, "crawl_page", crawl)
    monkeypatch.setattr(
        document_chunking,
        "chunk_documents",
        lambda documents, *_args: [
            {"content": item.page_content} for item in documents
        ],
    )
    version_store.crawl = crawl
    return version_store


def files(store):
    return {path for path in store.root.rglob("*") if path.is_file()}


async def test_embedding_failure_never_writes_a_website_blob(website_store):
    case = await website_store.make("website")
    original = files(website_store)
    website_store.provider.generate_embeddings_batch.side_effect = ValueError(
        "synthetic-provider-failure"
    )
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    assert files(website_store) == original
    revision, source, documents = await website_store.read(case)
    assert revision.state == "failed" and source.active_number == 1
    assert [item.id for item in documents] == [case.document]


async def test_cancelled_embedding_leaves_no_website_file(website_store):
    case = await website_store.make("website")
    original = files(website_store)
    website_store.provider.generate_embeddings_batch.side_effect = (
        asyncio.CancelledError()
    )
    with pytest.raises(asyncio.CancelledError):
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
    assert files(website_store) == original
    assert (await website_store.read(case))[1].active_number == 1


async def test_preparation_keeps_validated_website_content_in_memory(
    website_store,
):
    case = await website_store.make("website")
    original = files(website_store)
    snapshot = await preparation.prepare_snapshot(
        case.project, case.revision, case.attempt
    )
    assert snapshot.website_markdown == "新版网站资料"
    assert snapshot.storage_path is None
    assert snapshot.chunks and len(snapshot.chunks[0].embedding) == 1536
    assert files(website_store) == original


async def test_stale_attempt_cannot_leave_a_website_blob(website_store):
    case = await website_store.make("website")
    original = files(website_store)

    async def invalidate(contents):
        async with website_store.factory() as db:
            version = await db.get(KnowledgeVersion, case.revision)
            version.state = "unchanged"
            version.snapshot = VersionSnapshot().model_dump(mode="json")
            await db.commit()
        return [[0.5] * 1536 for _ in contents]

    website_store.provider.generate_embeddings_batch.side_effect = invalidate
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "skipped"
    )
    assert files(website_store) == original
    assert (await website_store.read(case))[1].active_number == 1


async def test_unchanged_website_does_not_write_or_embed(website_store):
    case = await website_store.make("website")
    original = files(website_store)
    website_store.crawl.return_value.content_markdown = "原商品资料"
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "unchanged"
    )
    assert files(website_store) == original
    website_store.provider_factory.assert_not_awaited()


async def test_publication_failure_removes_only_its_unreferenced_blob(
    website_store, monkeypatch
):
    case = await website_store.make("website", "publish")
    original = files(website_store)
    monkeypatch.setattr(
        preparation,
        "publish",
        AsyncMock(
            side_effect=VersionConflict("synthetic-publication-failure")
        ),
    )
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    assert files(website_store) == original
    assert (await website_store.read(case))[1].active_number == 1


@pytest.mark.parametrize(
    "action,state",
    [
        ("save", "ready"),
        ("submit", "pending_review"),
        ("publish", "published"),
    ],
)
async def test_successful_website_version_keeps_its_durable_body(
    website_store, action, state
):
    case = await website_store.make("website", action)
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == state
    )
    revision, source, documents = await website_store.read(case)
    path = Path(revision.snapshot["storage_path"])
    assert path.is_relative_to(website_store.root)
    assert ".knowledge-version-artifacts" in path.parts
    assert path.read_text(encoding="utf-8") == "新版网站资料"
    assert (path.parent / "manifest.json").is_file()
    assert revision.snapshot["website_markdown"] == "新版网站资料"
    assert source.active_number == (2 if action == "publish" else 1)
    assert len(documents) == 1
    assert documents[0].content == (
        "新版网站资料" if action == "publish" else "原商品资料"
    )


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_commit_result_determines_file_retention_even_after_interruption(
    website_store, monkeypatch, committed, cancelled
):
    case = await website_store.make("website", "publish")
    original = files(website_store)
    sessions = preparation.get_db_session
    interrupted = False

    @asynccontextmanager
    async def intercept():
        async with sessions() as db:
            commit = db.commit

            async def commit_with_interruption():
                nonlocal interrupted
                if not interrupted and list(
                    website_store.root.rglob("content.md")
                ):
                    interrupted = True
                    if committed:
                        await commit()
                    if cancelled:
                        raise asyncio.CancelledError()
                    raise ConnectionError("synthetic-commit-response-lost")
                await commit()

            monkeypatch.setattr(db, "commit", commit_with_interruption)
            yield db

    monkeypatch.setattr(preparation, "get_db_session", intercept)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await preparation.process_revision(
                case.project, case.revision, case.attempt
            )
    else:
        assert (
            await preparation.process_revision(
                case.project, case.revision, case.attempt
            )
            == "failed"
        )
    assert interrupted
    revision, source, documents = await website_store.read(case)
    assert source.active_number == (2 if committed else 1)
    if committed:
        assert revision.state == "published"
        assert Path(revision.snapshot["storage_path"]).is_file()
        assert documents[0].content == "新版网站资料"
    else:
        assert files(website_store) == original
        assert documents[0].id == case.document


@pytest.mark.parametrize("part", ["manifest.json", "content.md"])
async def test_partial_disk_write_is_recovered_without_losing_live_knowledge(
    website_store, monkeypatch, part
):
    from src.rag_service.services import knowledge_version_artifacts as storage

    case = await website_store.make("website", "publish")
    original = files(website_store)
    write = storage.write_new

    def interrupted_write(path, content):
        if path.name == part:
            path.write_bytes(content[:3])
            raise OSError("synthetic-write-failure")
        write(path, content)

    monkeypatch.setattr(storage, "write_new", interrupted_write)
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "failed"
    )
    assert files(website_store) == original
    assert (await website_store.read(case))[1].active_number == 1


@pytest.mark.parametrize("publication_fails", [False, True])
async def test_cleanup_outage_keeps_receipt_for_later_recovery(
    website_store, monkeypatch, publication_fails
):
    from src.rag_service.services.knowledge_version_artifact_cleanup import (
        collect_artifacts,
    )

    case = await website_store.make("website", "publish")
    with monkeypatch.context() as patch:
        patch.setattr(
            preparation,
            "cleanup_artifact",
            AsyncMock(
                side_effect=ConnectionError(
                    "synthetic-cleanup-database-outage"
                )
            ),
        )
        if publication_fails:
            patch.setattr(
                preparation,
                "publish",
                AsyncMock(
                    side_effect=VersionConflict(
                        "synthetic-publication-failure"
                    )
                ),
            )
        result = await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
    assert result == ("failed" if publication_fails else "published")
    receipts = list(website_store.root.rglob("manifest.json"))
    assert len(receipts) == 1 and (receipts[0].parent / "content.md").is_file()
    async with website_store.factory() as db:
        stats = await collect_artifacts(db, 100)
        await db.commit()
    assert stats["removed" if publication_fails else "retained"] == 1
    assert (
        receipts[0].parent / "content.md"
    ).exists() is not publication_fails
