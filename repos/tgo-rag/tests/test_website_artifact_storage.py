"""Private storage ownership, bounded recovery and task wiring contracts."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest

from src.rag_service.config import get_settings, Settings
from src.rag_service.models.knowledge_versions import KnowledgeVersionSource
from src.rag_service.services import knowledge_version_artifacts as storage


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    source = KnowledgeVersionSource(
        id=uuid4(), project_id=uuid4(), source_kind="website"
    )
    artifact = storage.website_artifact(source, uuid4(), uuid4())
    artifact.write("受控的网站正文")
    return artifact


def test_receipt_is_durable_and_contains_only_ownership_metadata(artifact):
    receipt = storage.read_receipt(artifact.directory)
    assert receipt == artifact.receipt
    payload = (artifact.directory / storage.RECEIPT).read_text(
        encoding="utf-8"
    )
    assert "受控的网站正文" not in payload
    assert artifact.path.read_text(encoding="utf-8") == "受控的网站正文"
    assert artifact.created


def test_existing_artifact_is_never_overwritten_or_claimed(artifact):
    other = storage.WebsiteArtifact(artifact.receipt, artifact.directory)
    with pytest.raises(FileExistsError):
        other.write("不能覆盖已有内容")
    assert not other.created
    assert artifact.path.read_text(encoding="utf-8") == "受控的网站正文"


def test_invalid_directory_is_rejected_before_creating_any_parent(artifact):
    outside = storage.artifact_root().parent / "outside"
    directory = outside / str(artifact.receipt.source) / str(uuid4())
    forged = storage.WebsiteArtifact(artifact.receipt, directory)
    with pytest.raises(ValueError):
        forged.write("不能写入")
    assert not outside.exists() and not forged.created


def test_nonmatching_reference_path_is_rejected_before_any_write(artifact):
    receipt = artifact.receipt.model_copy(update={"reference_path": "outside"})
    forged = storage.WebsiteArtifact(receipt, artifact.directory)
    with pytest.raises(ValueError):
        forged.write("不能覆盖")
    assert not forged.created
    assert artifact.path.read_text(encoding="utf-8") == "受控的网站正文"


@pytest.mark.parametrize("kind", ["file", "qa"])
def test_only_websites_can_create_website_artifacts(
    tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    source = KnowledgeVersionSource(
        id=uuid4(), project_id=uuid4(), source_kind=kind
    )
    with pytest.raises(ValueError):
        storage.website_artifact(source, uuid4(), uuid4())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "target", ["namespace", "source", "artifact", "manifest", "body"]
)
def test_aliases_and_links_are_rejected_without_touching_their_targets(
    artifact, monkeypatch, target
):
    paths = {
        "namespace": storage.artifact_root(),
        "source": artifact.directory.parent,
        "artifact": artifact.directory,
        "manifest": artifact.directory / storage.RECEIPT,
        "body": artifact.path,
    }
    path = paths[target]
    resolve = Path.resolve

    def redirected(candidate, *args, **kwargs):
        if candidate == path:
            return storage.artifact_root().parent / "outside"
        return resolve(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", redirected)
        with pytest.raises(ValueError):
            storage.remove_owned_files(artifact.directory)
    assert artifact.path.read_text(encoding="utf-8") == "受控的网站正文"


def test_unknown_children_make_deletion_refuse_the_entire_directory(artifact):
    extra = artifact.directory / "unknown.txt"
    extra.write_text("未知资料", encoding="utf-8")
    with pytest.raises(ValueError):
        storage.remove_owned_files(artifact.directory)
    assert artifact.path.is_file() and extra.is_file()


@pytest.mark.parametrize("value", [b"{partial", b"x" * 8193, b"\xff\xfe"])
def test_bad_receipts_are_bounded_and_never_reinterpreted_as_paths(
    artifact, value
):
    (artifact.directory / storage.RECEIPT).write_bytes(value)
    with pytest.raises(ValueError):
        storage.read_receipt(artifact.directory)
    assert artifact.path.is_file()


def test_only_the_owned_directory_is_removed_and_retries_are_idempotent(
    artifact,
):
    original = storage.artifact_root().parent / "original.md"
    original.write_text("原资料", encoding="utf-8")
    storage.remove_owned_files(artifact.directory)
    storage.remove_owned_files(artifact.directory)
    assert original.read_text(encoding="utf-8") == "原资料"
    assert not artifact.directory.exists()


def test_cursor_failure_preserves_old_cursor_and_removes_own_tempfile(
    artifact, monkeypatch
):
    key = (str(artifact.receipt.source), str(artifact.receipt.artifact))
    storage.save_cursor(key)
    with monkeypatch.context() as patch:
        patch.setattr(
            storage.os, "replace", Mock(side_effect=OSError("synthetic"))
        )
        with pytest.raises(OSError):
            storage.save_cursor(None)
    assert storage.read_cursor() == key
    assert not list(storage.artifact_root().glob("*.tmp"))
    assert artifact.path.is_file()


@pytest.mark.parametrize(
    "value", [b"{partial", b'{"key":["bad","names"]}', b"x" * 1025]
)
def test_bad_cursor_restarts_scanning_without_losing_artifacts(
    artifact, value
):
    (storage.artifact_root() / storage.CURSOR).write_bytes(value)
    assert storage.read_cursor() is None
    assert storage.scan_artifacts(1) == [artifact.directory]


@pytest.mark.parametrize("value", [0, 1001])
def test_cleanup_batch_has_enforced_bounds(artifact, value):
    with pytest.raises(ValueError):
        storage.scan_artifacts(value)


def test_cursor_recovery_removes_only_owned_stale_cursor_temporaries(artifact):
    owned = storage.artifact_root() / f".cleanup-cursor.{uuid4()}.tmp"
    unknown = storage.artifact_root() / ".cleanup-cursor.unknown.tmp"
    owned.write_bytes(b"partial")
    unknown.write_bytes(b"preserve")
    storage.save_cursor(None)
    assert not owned.exists() and unknown.read_bytes() == b"preserve"
    assert artifact.path.is_file()


@pytest.mark.parametrize(
    "field,value",
    [
        ("knowledge_version_cleanup_batch_size", 0),
        ("knowledge_version_cleanup_batch_size", 1001),
        ("knowledge_version_cleanup_interval_seconds", 59),
        ("knowledge_version_cleanup_interval_seconds", 86401),
    ],
)
def test_environment_configuration_rejects_unbounded_cleanup(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


def test_celery_cleanup_task_is_registered_routed_and_scheduled():
    from src.rag_service.tasks import knowledge_versions as tasks

    app = tasks.celery_app
    task = tasks.cleanup_knowledge_version_artifacts
    name = "cleanup_knowledge_version_artifacts"
    assert task.name == name and app.tasks[name].run == task.run
    assert app.conf.task_routes[name] == {"queue": "celery"}
    assert app.conf.beat_schedule["cleanup-knowledge-version-artifacts"] == {
        "task": name,
        "schedule": float(
            get_settings().knowledge_version_cleanup_interval_seconds
        ),
    }
    assert task.retry_kwargs == {"max_retries": 3} and task.retry_backoff


def test_real_celery_task_entry_executes_the_cleanup_and_closes_its_loop(
    monkeypatch,
):
    from src.rag_service.tasks import knowledge_versions as tasks

    stats = {
        "scanned": 1,
        "removed": 1,
        "retained": 0,
        "busy": 0,
        "invalid": 0,
        "failed": 0,
    }
    operation = AsyncMock(return_value=stats)
    close = AsyncMock()
    reset = Mock()
    monkeypatch.setattr(tasks, "_collect_artifacts", operation)
    monkeypatch.setattr(tasks, "close_database", close)
    monkeypatch.setattr(tasks, "reset_db_state", reset)
    result = tasks.cleanup_knowledge_version_artifacts.apply(throw=True)
    assert result.successful() and result.result == stats
    operation.assert_awaited_once()
    close.assert_awaited_once()
    assert reset.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("source_exists", [False, True])
async def test_missing_database_confirmation_keeps_the_file(
    artifact, source_exists
):
    from src.rag_service.services.knowledge_version_artifact_cleanup import (
        cleanup_artifact,
    )

    db = MagicMock()
    db.in_transaction.return_value = True
    source = (
        KnowledgeVersionSource(
            id=artifact.receipt.source,
            project_id=artifact.receipt.project,
            source_kind="website",
        )
        if source_exists
        else None
    )
    rows = MagicMock()
    rows.one_or_none.return_value = source
    db.scalars = AsyncMock(return_value=rows)
    db.scalar = AsyncMock(side_effect=["read committed", None])
    with pytest.raises(ValueError, match="不能确认"):
        await cleanup_artifact(db, artifact.directory)
    assert artifact.path.is_file()


def test_task_retry_never_exposes_original_failure_body(monkeypatch):
    from celery.exceptions import Retry
    from src.rag_service.tasks import knowledge_versions as tasks

    private = "fixture-private-database-failure"
    operation = AsyncMock(side_effect=ConnectionError(private))
    close = AsyncMock()
    monkeypatch.setattr(tasks, "_collect_artifacts", operation)
    monkeypatch.setattr(tasks, "close_database", close)
    monkeypatch.setattr(tasks, "reset_db_state", Mock())
    with pytest.raises((RuntimeError, Retry)) as captured:
        tasks.cleanup_knowledge_version_artifacts.apply(throw=True)
    error = (
        captured.value.exc
        if isinstance(captured.value, Retry)
        else captured.value
    )
    assert isinstance(error, RuntimeError) and private not in str(error)
    assert operation.await_count >= 1 and close.await_count >= 1


def test_successful_task_closes_loop_even_if_database_disposal_fails(
    monkeypatch,
):
    from src.rag_service.tasks import knowledge_versions as tasks

    stats = {
        "scanned": 0,
        "removed": 0,
        "retained": 0,
        "busy": 0,
        "invalid": 0,
        "failed": 0,
    }
    monkeypatch.setattr(
        tasks, "_collect_artifacts", AsyncMock(return_value=stats)
    )
    monkeypatch.setattr(
        tasks,
        "close_database",
        AsyncMock(side_effect=OSError("fixture-private")),
    )
    reset = Mock()
    monkeypatch.setattr(tasks, "reset_db_state", reset)
    assert (
        tasks.cleanup_knowledge_version_artifacts.apply(throw=True).result
        == stats
    )
    assert reset.call_count == 2
