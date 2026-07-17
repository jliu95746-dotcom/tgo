from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.services.media.storage import EncryptedLocalMediaStorage
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
