"""Restart-safe cleanup only removes old generated images with no DB owner."""

import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_im_access import records as records
from tests.test_visitor_avatar_access import avatar_api as avatar_api
from tests.test_visitor_profile_access import profile_api as profile_api


DAY_SECONDS = 24 * 60 * 60
GENERATED_OLD = "1760000000000_aaaaaaaaaaaaaaaa.png"
GENERATED_CURRENT = "1760000000001_bbbbbbbbbbbbbbbb.png"
GENERATED_RECENT = "1760000000002_cccccccccccccccc.png"


def older_than_retention(path: Path) -> None:
    timestamp = time.time() - 8 * DAY_SECONDS
    os.utime(path, (timestamp, timestamp))


def test_cleanup_keeps_current_and_recent_avatar(avatar_api):
    from app.services.visitor_avatar_retention import cleanup_orphan_visitor_avatars

    _, db, _, visitors, _, _, original = avatar_api
    visitor = visitors[0]
    old = original.parent / GENERATED_OLD
    current = original.parent / GENERATED_CURRENT
    recent = original.parent / GENERATED_RECENT
    for path in (old, current, recent):
        path.write_bytes(b"avatar-image")
    older_than_retention(old)
    older_than_retention(current)
    visitor.avatar_url = f"/v1/visitors/{visitor.id}/avatar?v={current.name}"
    db.commit()

    result = cleanup_orphan_visitor_avatars(db, now=time.time(), older_than_seconds=7 * DAY_SECONDS)
    assert result.deleted == 1 and result.errors == 0
    assert not old.exists()
    assert current.read_bytes() == b"avatar-image"
    assert recent.read_bytes() == b"avatar-image"
    assert original.read_bytes() == b"saved-test-image"


def test_cleanup_preserves_ambiguous_legacy_and_foreign_project(avatar_api):
    from app.services.visitor_avatar_retention import cleanup_orphan_visitor_avatars

    _, db, _, visitors, _, _, original = avatar_api
    visitor = visitors[0]
    legacy = original.parent / GENERATED_OLD
    legacy.write_bytes(b"legacy-image")
    older_than_retention(legacy)
    visitor.avatar_url = f"/v1/visitors/{visitor.id}/avatar"
    db.commit()

    wrong_project = original.parent.parent / str(visitors[1].id)
    wrong_project.mkdir()
    foreign = wrong_project / GENERATED_CURRENT
    foreign.write_bytes(b"foreign-image")
    older_than_retention(foreign)

    result = cleanup_orphan_visitor_avatars(db, now=time.time(), older_than_seconds=7 * DAY_SECONDS)
    assert result.deleted == 0
    assert legacy.exists() and foreign.exists()


def test_cleanup_removes_old_crash_file_for_deleted_visitor(avatar_api):
    from app.services.visitor_avatar_retention import cleanup_orphan_visitor_avatars

    _, db, _, visitors, _, _, original = avatar_api
    deleted_visitor = original.parent.parent / str(uuid4())
    deleted_visitor.mkdir()
    orphan = deleted_visitor / GENERATED_OLD
    orphan.write_bytes(b"uncommitted-image")
    older_than_retention(orphan)

    result = cleanup_orphan_visitor_avatars(db, now=time.time(), older_than_seconds=7 * DAY_SECONDS)
    assert result.deleted == 1
    assert not orphan.exists()
    assert original.exists()


def test_cleanup_retries_file_error_without_touching_current(avatar_api, monkeypatch):
    from app.services.visitor_avatar_retention import cleanup_orphan_visitor_avatars

    _, db, _, visitors, _, _, original = avatar_api
    orphan = original.parent / GENERATED_OLD
    orphan.write_bytes(b"uncommitted-image")
    older_than_retention(orphan)
    unlink = Path.unlink

    def deny_once(path: Path, *args, **kwargs):
        if path == orphan:
            raise PermissionError("fixture denied")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", deny_once)
        first = cleanup_orphan_visitor_avatars(db, now=time.time(), older_than_seconds=7 * DAY_SECONDS)
    assert first.deleted == 0 and first.errors == 1
    assert orphan.exists() and original.exists()

    second = cleanup_orphan_visitor_avatars(db, now=time.time(), older_than_seconds=7 * DAY_SECONDS)
    assert second.deleted == 1 and second.errors == 0
    assert not orphan.exists() and original.exists()


@pytest.mark.asyncio
async def test_retention_task_can_be_disabled_and_stopped(monkeypatch):
    from app.tasks import visitor_avatar_retention as task

    started = asyncio.Event()

    async def wait_until_stopped():
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(task, "_task", None)
    monkeypatch.setattr(task, "_run_periodically", wait_until_stopped)
    monkeypatch.setattr(task.settings, "VISITOR_AVATAR_CLEANUP_ENABLED", False)
    task.start_visitor_avatar_retention()
    assert task._task is None

    monkeypatch.setattr(task.settings, "VISITOR_AVATAR_CLEANUP_ENABLED", True)
    task.start_visitor_avatar_retention()
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert task._task is not None
    finally:
        await task.stop_visitor_avatar_retention()
    assert task._task is None
