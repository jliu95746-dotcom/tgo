"""Media must use scoped stored bytes, not a customer URL or placeholder."""

from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import ChatFile
from app.schemas.chat_media import ChatMediaInput
from app.services.chat_media_service import ChatMediaService, MediaInputError
from app.services.storage.local import LocalStorageBackend
from app.utils.encoding import build_visitor_channel_id


@pytest.fixture
def media(tmp_path):
    engine = create_engine("sqlite://")
    ChatFile.__table__.create(engine)
    with Session(engine) as db:
        project, platform, visitor, file_id = [uuid4() for _ in range(4)]
        channel = build_visitor_channel_id(visitor)
        path = f"chat/{project}/251/{channel}/image.png"
        content = b"\x89PNG\r\n\x1a\nowned-test-image"
        absolute = tmp_path / path
        absolute.parent.mkdir(parents=True)
        absolute.write_bytes(content)
        row = ChatFile(
            id=file_id,
            project_id=project,
            channel_id=channel,
            channel_type=251,
            file_name="image.png",
            file_path=path,
            file_type="image/png",
            file_size=len(content),
            uploaded_by_platform_id=platform,
        )
        db.add(row)
        db.commit()
        request = ChatMediaInput(
            project_id=project,
            platform_id=platform,
            visitor_id=visitor,
            source_message_id="incoming-1",
            message_type=2,
            reference=f"/v1/chat/files/{file_id}",
        )
        storage = LocalStorageBackend(str(tmp_path), "http://storage.test")
        yield db, row, request, storage, content
    engine.dispose()


@pytest.mark.asyncio
async def test_scoped_bytes_are_loaded_and_hashed(media):
    db, row, request, storage, content = media
    loaded = await ChatMediaService(db, storage=storage).load(request)
    assert loaded.content == content
    assert loaded.file_id == row.id
    assert loaded.sha256 == sha256(content).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["project", "platform", "visitor", "deleted", "channel_type"]
)
async def test_foreign_or_deleted_file_never_reaches_storage(media, change):
    db, row, request, storage, _ = media
    if change == "deleted":
        from datetime import datetime, timezone

        row.deleted_at = datetime.now(timezone.utc)
    elif change == "channel_type":
        row.channel_type = 1
    else:
        field = {
            "project": "project_id",
            "platform": "uploaded_by_platform_id",
            "visitor": "channel_id",
        }[change]
        setattr(
            row,
            field,
            build_visitor_channel_id(uuid4()) if change == "visitor" else uuid4(),
        )
    db.commit()
    storage.read = AsyncMock()
    with pytest.raises(MediaInputError):
        await ChatMediaService(db, storage=storage).load(request)
    storage.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_arbitrary_url_and_mismatched_explicit_file_are_rejected(media):
    db, _, request, storage, _ = media
    for changed in [
        request.model_copy(update={"reference": "http://127.0.0.1/private"}),
        request.model_copy(update={"file_id": uuid4()}),
    ]:
        with pytest.raises(MediaInputError):
            await ChatMediaService(db, storage=storage).load(changed)


@pytest.mark.asyncio
async def test_storage_path_escape_and_size_change_are_rejected(media):
    db, row, request, storage, _ = media
    row.file_size += 1
    db.commit()
    with pytest.raises(MediaInputError):
        await ChatMediaService(db, storage=storage).load(request)
    row.file_path = "../outside.txt"
    db.commit()
    with pytest.raises(MediaInputError):
        await ChatMediaService(db, storage=storage).load(request)


@pytest.mark.asyncio
async def test_local_read_is_bounded_and_confined(tmp_path):
    storage = LocalStorageBackend(str(tmp_path), "http://storage.test")
    (tmp_path / "large").write_bytes(b"a" * 11)
    with pytest.raises(ValueError):
        await storage.read("large", max_bytes=10)
    for path in ["../secret", str(Path(tmp_path).parent / "secret")]:
        with pytest.raises(ValueError):
            await storage.read(path, max_bytes=10)
