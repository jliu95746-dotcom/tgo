"""Real PostgreSQL fences protect committed files from artifact recovery."""

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select, text

from src.rag_service.models import File
from src.rag_service.models.knowledge_versions import (
    KnowledgeVersion,
    KnowledgeVersionSource,
)
from src.rag_service.schemas.knowledge_versions import (
    VersionActorRequest,
    VersionChangeRequest,
    VersionSnapshot,
)
from src.rag_service.services import knowledge_version_artifacts as storage
from src.rag_service.services.knowledge_version_artifact_cleanup import (
    CLEANUP_LOCK,
    cleanup_artifact,
    collect_artifacts,
)
from src.rag_service.services.knowledge_versions import create_change
from tests import test_knowledge_version_recovery as recovery
from tests import test_website_revision_artifacts as revision_tests

version_database = recovery.version_database
version_store = recovery.version_store
website_store = revision_tests.website_store
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TGO_VERSION_PREPARE_TEST_DATABASE_URL"),
        reason="Requires owned version preparation PostgreSQL database",
    ),
]


async def artifact_for(store, case):
    async with store.factory() as db:
        source = await db.scalar(
            select(KnowledgeVersionSource)
            .where(KnowledgeVersionSource.id == case.source)
            .with_for_update()
        )
        artifact = storage.website_artifact(
            source, case.revision, case.attempt
        )
        artifact.write("尚未提交的网站正文")
        await db.commit()
        return artifact


async def collect(store, limit=100):
    async with store.factory() as db:
        result = await collect_artifacts(db, limit)
        await db.commit()
        return result


async def test_fresh_session_recovers_abandoned_receipt_after_worker_exit(
    website_store,
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    directory = artifact.directory
    del artifact
    result = await collect(website_store)
    assert result["removed"] == 1 and result["failed"] == 0
    assert not directory.exists()
    revision, source, documents = await website_store.read(case)
    assert revision.state == "processing" and source.active_number == 1
    assert documents[0].id == case.document


@pytest.mark.parametrize(
    "reference",
    [
        "snapshot",
        "file",
        "old-posix-mount",
        "old-windows-mount",
        "case-variant",
    ],
)
async def test_any_persisted_reference_keeps_the_payload(
    website_store, reference
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    path = str(artifact.path)
    if reference == "old-posix-mount":
        path = "/older-upload-root/" + "/".join(artifact.path.parts[-4:])
    elif reference == "old-windows-mount":
        path = "Z:\\older-upload-root\\" + "\\".join(artifact.path.parts[-4:])
    elif reference == "case-variant":
        path = path.upper()
    async with website_store.factory() as db:
        if reference == "snapshot":
            # A different source/tenant reference still protects shared bytes.
            other = await website_store.make("qa")
            version = await db.get(KnowledgeVersion, other.revision)
            version.snapshot = VersionSnapshot(storage_path=path).model_dump(
                mode="json"
            )
        else:
            db.add(
                File(
                    project_id=uuid4(),
                    collection_id=None,
                    original_filename="被引用的文件.md",
                    file_size=32,
                    content_type="text/markdown",
                    storage_provider="local",
                    storage_path=path,
                    status="completed",
                )
            )
        await db.commit()
    result = await collect(website_store)
    assert result["retained"] == 1 and result["removed"] == 0
    assert artifact.path.read_text(encoding="utf-8") == "尚未提交的网站正文"


async def test_cleanup_cannot_cross_an_inflight_source_lock(website_store):
    case = await website_store.make("website")
    async with website_store.factory() as writer:
        source = await writer.scalar(
            select(KnowledgeVersionSource)
            .where(KnowledgeVersionSource.id == case.source)
            .with_for_update()
        )
        artifact = storage.website_artifact(
            source, case.revision, case.attempt
        )
        artifact.write("即将提交的网站正文")
        result = await collect(website_store)
        assert result["busy"] == 1 and result["removed"] == 0
        assert artifact.path.is_file()
        version = await writer.get(KnowledgeVersion, case.revision)
        version.snapshot = VersionSnapshot(
            storage_path=str(artifact.path)
        ).model_dump(mode="json")
        await writer.commit()
    assert (await collect(website_store))["retained"] == 1
    assert artifact.path.is_file()


async def test_overlapping_housekeeping_job_skips_without_deleting(
    website_store,
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    async with website_store.factory() as holder:
        await holder.execute(select(func.pg_advisory_xact_lock(CLEANUP_LOCK)))
        result = await collect(website_store)
        assert result["busy"] == 1 and result["scanned"] == 0
        assert artifact.path.is_file()
        await holder.rollback()
    assert (await collect(website_store))["removed"] == 1


async def test_deleted_source_allows_cleanup_but_does_not_delete_original_file(
    website_store,
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    async with website_store.factory() as db:
        source = await db.get(KnowledgeVersionSource, case.source)
        await db.delete(source)
        await db.commit()
    result = await collect(website_store)
    assert result["removed"] == 1 and not artifact.directory.exists()
    assert list(website_store.root.glob("*.md"))


@pytest.mark.parametrize("body_exists", [False, True])
async def test_corrupt_receipt_only_recovers_when_no_body_is_at_risk(
    website_store, body_exists
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    (artifact.directory / storage.RECEIPT).write_bytes(b"{partial")
    if not body_exists:
        artifact.path.unlink()
    result = await collect(website_store)
    assert result["invalid" if body_exists else "removed"] == 1
    assert artifact.directory.exists() is body_exists


@pytest.mark.parametrize("problem", ["project", "source", "extra-file"])
async def test_unknown_metadata_and_files_are_preserved(
    website_store, problem
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    if problem == "extra-file":
        (artifact.directory / "unknown.txt").write_text(
            "保留的未知文件", encoding="utf-8"
        )
    else:
        receipt = artifact.receipt.model_copy(update={problem: uuid4()})
        (artifact.directory / storage.RECEIPT).write_text(
            receipt.model_dump_json(), encoding="utf-8"
        )
    result = await collect(website_store)
    assert result["invalid"] == 1 and result["removed"] == 0
    assert artifact.path.is_file()


async def test_empty_directory_created_before_crash_is_recovered(
    website_store,
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    artifact.path.unlink()
    (artifact.directory / storage.RECEIPT).unlink()
    assert (await collect(website_store))["removed"] == 1
    assert not artifact.directory.exists()


async def test_cursor_moves_past_referenced_files_in_bounded_batches(
    website_store,
):
    case = await website_store.make("website")
    artifacts = sorted(
        [await artifact_for(website_store, case) for _ in range(3)],
        key=lambda item: str(item.directory),
    )
    async with website_store.factory() as db:
        for artifact in artifacts[:2]:
            db.add(
                File(
                    project_id=case.project,
                    collection_id=None,
                    original_filename="保留.md",
                    file_size=32,
                    content_type="text/markdown",
                    storage_provider="local",
                    storage_path=str(artifact.path),
                    status="completed",
                )
            )
        await db.commit()
    outcomes = [await collect(website_store, 1) for _ in range(3)]
    assert all(item["scanned"] == 1 for item in outcomes)
    assert sum(item["retained"] for item in outcomes) == 2
    assert sum(item["removed"] for item in outcomes) == 1
    assert all(item.path.is_file() for item in artifacts[:2])
    assert not artifacts[2].directory.exists()


async def test_filesystem_failure_can_be_retried(website_store, monkeypatch):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    unlink = Path.unlink

    def fail_body(path, *args, **kwargs):
        if path == artifact.path:
            raise PermissionError("synthetic-filesystem-failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_body)
        result = await collect(website_store)
    assert result["failed"] == 1 and artifact.path.is_file()
    assert (await collect(website_store))["removed"] == 1


async def test_repeatable_read_transaction_cannot_use_stale_reference_snapshot(
    website_store,
):
    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    async with website_store.factory() as db:
        await db.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        )
        with pytest.raises(ValueError, match="最新状态"):
            await cleanup_artifact(db, artifact.directory)
    assert artifact.path.is_file()


async def test_legacy_files_outside_namespace_are_preserved(
    website_store,
):
    case = await website_store.make("website")
    legacy = website_store.root / f"version-{case.attempt}.md"
    legacy.write_text("无法证明归属的旧文件", encoding="utf-8")
    async with website_store.factory() as db:
        assert await cleanup_artifact(db, legacy.parent) == "invalid"
    assert (await collect(website_store))["scanned"] == 0
    assert legacy.read_text(encoding="utf-8") == "无法证明归属的旧文件"


async def test_registered_task_runs_real_database_and_filesystem_cleanup(
    website_store, monkeypatch
):
    from src.rag_service.tasks import knowledge_versions as tasks

    case = await website_store.make("website")
    artifact = await artifact_for(website_store, case)
    monkeypatch.setattr(
        tasks, "get_db_session", revision_tests.preparation.get_db_session
    )
    # The scoped test factory owns NullPool connections, not the global engine.
    monkeypatch.setattr(tasks, "close_database", AsyncMock())
    monkeypatch.setattr(tasks, "reset_db_state", Mock())

    def execute():
        result = tasks.cleanup_knowledge_version_artifacts.apply(throw=True)
        assert result.successful()
        return result.result

    result = await asyncio.to_thread(execute)
    assert result["removed"] == 1 and result["failed"] == 0
    assert not artifact.directory.exists()
    assert (await website_store.read(case))[1].active_number == 1


@pytest.mark.parametrize("action", ["save", "submit"])
async def test_existing_draft_record_protects_body_even_with_a_broken_snapshot(
    website_store, action
):
    case = await website_store.make("website", action)
    assert await revision_tests.preparation.process_revision(
        case.project, case.revision, case.attempt
    ) == ("ready" if action == "save" else "pending_review")
    async with website_store.factory() as db:
        version = await db.get(KnowledgeVersion, case.revision)
        body = Path(version.snapshot["storage_path"])
        version.snapshot = {"attempt": "synthetic-broken-snapshot"}
        await db.commit()
    result = await collect(website_store)
    assert result["retained"] == 1 and result["removed"] == 0
    assert body.read_text(encoding="utf-8") == "新版网站资料"


async def test_retired_record_protects_its_only_body_after_snapshot_corruption(
    website_store,
):
    case = await website_store.make("website", "publish")
    assert (
        await revision_tests.preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "published"
    )
    async with website_store.factory() as db:
        previous = await db.get(KnowledgeVersion, case.revision)
        previous_path = Path(previous.snapshot["storage_path"])
        version = await create_change(
            db,
            case.project,
            "website",
            case.parent,
            VersionChangeRequest(actor="测试员工", action="publish"),
        )
        identifier, attempt = version.id, UUID(version.snapshot["attempt"])
        await db.commit()
    website_store.crawl.return_value.content_markdown = "更新后的网站资料"
    assert (
        await revision_tests.preparation.process_revision(
            case.project, identifier, attempt
        )
        == "published"
    )
    async with website_store.factory() as db:
        previous = await db.get(KnowledgeVersion, case.revision)
        assert previous.state == "retired"
        previous.snapshot = {"attempt": "synthetic-broken-snapshot"}
        await db.commit()
    result = await collect(website_store)
    assert result["retained"] == 2 and result["removed"] == 0
    assert previous_path.read_text(encoding="utf-8") == "新版网站资料"


async def test_explicit_discard_releases_draft_artifact_for_cleanup(
    website_store,
):
    from src.rag_service.routers.knowledge_versions import transition

    case = await website_store.make("website", "save")
    assert (
        await revision_tests.preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "ready"
    )
    async with website_store.factory() as db:
        version = await db.get(KnowledgeVersion, case.revision)
        path = Path(version.snapshot["storage_path"])
        result = await transition(
            case.revision,
            "discard",
            VersionActorRequest(actor="测试管理员"),
            project_id=case.project,
            db=db,
        )
        assert result.state == "unchanged"
    assert (await collect(website_store))["removed"] == 1
    assert not path.exists()
    assert (await website_store.read(case))[1].active_number == 1
