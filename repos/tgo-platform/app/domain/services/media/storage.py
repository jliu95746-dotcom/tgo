from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.services.media.types import DownloadedMedia, StoredMediaObject


_FILE_HEADER = b"TGOMEDIA1"


class MediaStorage(Protocol):
    def object_key_for(
        self,
        *,
        platform_id: uuid.UUID,
        inbox_id: uuid.UUID,
        attempt_id: str,
        media: DownloadedMedia,
    ) -> str: ...

    async def put(
        self,
        *,
        object_key: str,
        media: DownloadedMedia,
    ) -> StoredMediaObject: ...

    async def get(self, *, object_key: str) -> bytes: ...

    async def delete(self, *, object_key: str) -> None: ...


class MediaObjectDeleter(Protocol):
    async def delete(self, *, object_key: str) -> None: ...


class LocalMediaObjectDeleter:
    """Delete local media objects without requiring a decryption key."""

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve()

    async def delete(self, *, object_key: str) -> None:
        path = self._resolve_object_path(object_key)
        await asyncio.to_thread(path.unlink, True)

    def _resolve_object_path(self, object_key: str) -> Path:
        candidate = self._root.joinpath(*PurePosixPath(object_key).parts).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise ValueError("media object key escapes the configured storage root")
        return candidate


class EncryptedLocalMediaStorage(LocalMediaObjectDeleter):
    """Development storage using AES-256-GCM and non-public object keys."""

    def __init__(self, *, root: Path, encryption_key: bytes, key_id: str) -> None:
        if len(encryption_key) != 32:
            raise ValueError("media encryption key must contain exactly 32 bytes")
        super().__init__(root=root)
        self._cipher = AESGCM(encryption_key)
        self._key_id = key_id

    def object_key_for(
        self,
        *,
        platform_id: uuid.UUID,
        inbox_id: uuid.UUID,
        attempt_id: str,
        media: DownloadedMedia,
    ) -> str:
        normalized_attempt_id = "".join(
            character for character in attempt_id if character.isalnum()
        )[:64]
        if not normalized_attempt_id:
            raise ValueError("media storage attempt_id is invalid")
        return PurePosixPath(
            "wecom",
            platform_id.hex,
            inbox_id.hex,
            normalized_attempt_id,
            f"{media.sha256[:32]}.{media.extension}.enc",
        ).as_posix()

    async def put(
        self,
        *,
        object_key: str,
        media: DownloadedMedia,
    ) -> StoredMediaObject:
        target = self._resolve_object_path(object_key)
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(
            nonce,
            media.content,
            object_key.encode("utf-8"),
        )
        await asyncio.to_thread(
            self._write_atomic,
            target,
            _FILE_HEADER + nonce + encrypted,
        )
        return StoredMediaObject(
            provider="local_encrypted",
            object_key=object_key,
            encryption_mode="aes-256-gcm",
            encryption_key_id=self._key_id,
        )

    async def get(self, *, object_key: str) -> bytes:
        encrypted = await asyncio.to_thread(
            self._resolve_object_path(object_key).read_bytes
        )
        header_size = len(_FILE_HEADER)
        nonce_end = header_size + 12
        if len(encrypted) <= nonce_end or encrypted[:header_size] != _FILE_HEADER:
            raise ValueError("stored media object has an invalid encrypted header")
        nonce = encrypted[header_size:nonce_end]
        return self._cipher.decrypt(
            nonce,
            encrypted[nonce_end:],
            object_key.encode("utf-8"),
        )

    @staticmethod
    def _write_atomic(target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".tmp-{uuid.uuid4().hex}")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
