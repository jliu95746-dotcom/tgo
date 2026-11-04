from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.services.media.storage import (
    EncryptedLocalMediaStorage,
    LocalMediaObjectDeleter,
)
from app.domain.services.media.types import DownloadedMedia


@pytest.mark.asyncio
async def test_local_storage_encrypts_media_and_uses_non_sensitive_key(
    tmp_path: Path,
) -> None:
    storage = EncryptedLocalMediaStorage(
        root=tmp_path,
        encryption_key=b"k" * 32,
        key_id="test-key",
    )
    plaintext = b"\xff\xd8\xffprivate-customer-image"
    media = DownloadedMedia(
        content=plaintext,
        mime_type="image/jpeg",
        extension="jpg",
        sha256="a" * 64,
    )
    object_key = storage.object_key_for(
        platform_id=uuid4(),
        inbox_id=uuid4(),
        attempt_id="claim-token",
        media=media,
    )

    stored = await storage.put(
        object_key=object_key,
        media=media,
    )

    stored_path = tmp_path.joinpath(*stored.object_key.split("/"))
    encrypted = stored_path.read_bytes()
    assert stored.provider == "local_encrypted"
    assert stored.encryption_mode == "aes-256-gcm"
    assert "media-id" not in stored.object_key
    assert encrypted.startswith(b"TGOMEDIA1")
    assert plaintext not in encrypted
    assert await storage.get(object_key=stored.object_key) == plaintext
    await storage.delete(object_key=stored.object_key)
    assert not stored_path.exists()


def test_local_storage_rejects_invalid_key_length(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        EncryptedLocalMediaStorage(
            root=tmp_path,
            encryption_key=b"too-short",
            key_id="test-key",
        )


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_atomic_write_files(tmp_path: Path) -> None:
    storage = LocalMediaObjectDeleter(root=tmp_path)
    media_dir = tmp_path / "wecom" / uuid4().hex / uuid4().hex / uuid4().hex
    media_dir.mkdir(parents=True)
    stale = media_dir / f".tmp-{'a' * 32}"
    recent = media_dir / f".tmp-{'b' * 32}"
    committed = media_dir / f"{'c' * 32}.jpg.enc"
    unrelated = media_dir / ".tmp-unrecognized"
    unrelated_dir = tmp_path / "wecom" / "misc"
    unrelated_dir.mkdir()
    unrelated_temporary = unrelated_dir / f".tmp-{'d' * 32}"
    for path in (stale, recent, committed, unrelated):
        path.write_bytes(b"encrypted")
    unrelated_temporary.write_bytes(b"unrelated")
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    old_time = (cutoff - timedelta(hours=1)).timestamp()
    for path in (stale, committed, unrelated, unrelated_temporary):
        os.utime(path, (old_time, old_time))

    removed = await storage.cleanup_expired_temporary_objects(
        older_than=cutoff, limit=10
    )

    assert removed == 1
    assert not stale.exists()
    assert recent.exists()
    assert committed.exists()
    assert unrelated.exists()
    assert unrelated_temporary.exists()


@pytest.mark.asyncio
async def test_cleanup_limits_batch_and_does_not_follow_directory_symlinks(
    tmp_path: Path,
) -> None:
    storage = LocalMediaObjectDeleter(root=tmp_path)
    media_dir = tmp_path / "wecom" / uuid4().hex / uuid4().hex / uuid4().hex
    media_dir.mkdir(parents=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    old_time = (cutoff - timedelta(hours=1)).timestamp()
    for index in range(2):
        path = media_dir / f".tmp-{index:032x}"
        path.write_bytes(b"encrypted")
        os.utime(path, (old_time, old_time))
    outside = tmp_path.parent / f"outside-media-{uuid4().hex}"
    outside.mkdir()
    outside_file = outside / f".tmp-{'f' * 32}"
    outside_file.write_bytes(b"outside")
    os.utime(outside_file, (old_time, old_time))
    try:
        (media_dir / "outside-link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass

    try:
        assert (
            await storage.cleanup_expired_temporary_objects(older_than=cutoff, limit=1)
            == 1
        )
        assert len(list(media_dir.glob(".tmp-*"))) == 1
        assert outside_file.exists()
    finally:
        outside_file.unlink()
        outside.rmdir()
