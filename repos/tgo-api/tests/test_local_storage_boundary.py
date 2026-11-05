"""All local object operations remain within the configured storage root."""

from io import BytesIO
from pathlib import Path

import pytest

from app.services.storage.local import LocalStorageBackend


UNSAFE_KEYS = (
    "",
    ".",
    "..",
    "../outside.txt",
    "inside/../../outside.txt",
    "/outside.txt",
    "inside/../outside.txt",
    "inside//file.txt",
    "inside/./file.txt",
    "inside/",
    "..\\outside.txt",
    "bad\x00name",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", UNSAFE_KEYS)
async def test_invalid_keys_cannot_write_delete_or_probe(tmp_path, key):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"synthetic-private-content")
    root = tmp_path / "objects"
    backend = LocalStorageBackend(str(root), "https://api.example.invalid")
    with pytest.raises(ValueError):
        await backend.upload(BytesIO(b"untrusted"), key, "text/plain")
    assert await backend.delete(key) is False
    assert await backend.exists(key) is False
    assert outside.read_bytes() == b"synthetic-private-content"
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "C:/outside.txt",
        "C:outside.txt",
        "\\\\server\\share\\outside.txt",
        "file.txt:stream",
    ],
)
async def test_foreign_volume_and_stream_keys_are_rejected_before_io(
    tmp_path, monkeypatch, key
):
    backend = LocalStorageBackend(str(tmp_path), "https://api.example.invalid")

    def unexpected_io(*args, **kwargs):
        pytest.fail("unsafe volume or stream keys must be rejected before IO")

    # Protect the host and network even when exercising the old implementation.
    with monkeypatch.context() as guard:
        guard.setattr(Path, "mkdir", unexpected_io)
        guard.setattr(Path, "exists", unexpected_io)
        with pytest.raises(ValueError):
            await backend.upload(BytesIO(b"untrusted"), key, "text/plain")
        assert await backend.delete(key) is False
        assert await backend.exists(key) is False
        with pytest.raises(ValueError):
            await backend.read(key, max_bytes=100)


@pytest.mark.asyncio
async def test_owned_files_support_upload_bounded_read_and_delete(tmp_path):
    backend = LocalStorageBackend(str(tmp_path), "https://api.example.invalid")
    key = "chat/owned-project/客户资料 原图%2E.txt"
    value = "仅测试资料".encode()
    url = await backend.upload(BytesIO(value), key, "text/plain")
    assert url == "https://api.example.invalid/v1/chat/files/" + key
    assert await backend.exists(key) is True
    assert await backend.read(key, max_bytes=len(value)) == value
    with pytest.raises(ValueError):
        await backend.read(key, max_bytes=len(value) - 1)
    assert await backend.delete(key) is True
    assert await backend.exists(key) is False
    assert await backend.delete(key) is False


@pytest.mark.asyncio
async def test_storage_directories_are_not_file_objects(tmp_path):
    backend = LocalStorageBackend(str(tmp_path), "https://api.example.invalid")
    (tmp_path / "owned-directory").mkdir()
    assert await backend.exists("owned-directory") is False
    assert await backend.delete("owned-directory") is False
    with pytest.raises(ValueError):
        await backend.read("owned-directory", max_bytes=100)
    assert (tmp_path / "owned-directory").is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", [False, True])
async def test_symlinks_cannot_reach_files_outside_root(tmp_path, directory):
    root, outside = tmp_path / "objects", tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"synthetic-private-content")
    backend = LocalStorageBackend(str(root), "https://api.example.invalid")
    link = root / "linked"
    link.symlink_to(
        outside if directory else sentinel, target_is_directory=directory
    )
    key = "linked/sentinel.txt" if directory else "linked"
    with pytest.raises(ValueError):
        await backend.upload(BytesIO(b"untrusted"), key, "text/plain")
    with pytest.raises(ValueError):
        await backend.read(key, max_bytes=100)
    assert await backend.delete(key) is False
    assert await backend.exists(key) is False
    assert sentinel.read_bytes() == b"synthetic-private-content"
    assert link.is_symlink()


@pytest.mark.asyncio
async def test_absolute_root_path_is_never_an_object_key(tmp_path):
    root = tmp_path / "objects"
    backend = LocalStorageBackend(str(root), "https://api.example.invalid")
    key = str(Path(root).resolve())
    with pytest.raises(ValueError):
        await backend.upload(BytesIO(b"untrusted"), key, "text/plain")
    assert await backend.delete(key) is False
    assert await backend.exists(key) is False
    assert root.is_dir()
