from __future__ import annotations

from uuid import uuid4

import pytest

from app.api import wecom_utils
from app.db.models import MediaProcessingJob, MessageMedia, WeComInbox
from app.domain.services.media.types import WeComMediaReference


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


@pytest.mark.parametrize(
    ("msgtype", "supported"),
    [("image", True), ("voice", True), ("video", False), ("file", False)],
)
def test_extracts_typed_media_reference(msgtype: str, supported: bool) -> None:
    payload: dict[str, object] = {"media_id": f"{msgtype}-media"}
    if msgtype == "file":
        payload.update({"file_name": "report.pdf", "file_size": 123})

    reference = wecom_utils._extract_kf_media_reference(
        {"msgtype": msgtype, msgtype: payload}
    )

    assert reference is not None
    assert reference.source_media_id == f"{msgtype}-media"
    assert reference.media_type == msgtype
    assert reference.supported is supported


def test_supported_media_without_media_id_returns_no_reference() -> None:
    assert (
        wecom_utils._extract_kf_media_reference(
            {"msgtype": "image", "image": {}}
        )
        is None
    )


@pytest.mark.asyncio
async def test_supported_media_creates_inbox_media_and_job_atomically() -> None:
    session = RecordingSession()
    platform_id = uuid4()

    result = await wecom_utils.try_store_wecom_inbox(
        session,
        media_reference=WeComMediaReference(
            source_media_id="media-id",
            media_type="image",
            supported=True,
        ),
        platform_id=platform_id,
        message_id="message-id",
        source_type="wecom_kf",
        from_user="external-user",
        msg_type="image",
        content="[image] media-id",
        status="pending_media",
    )

    assert result == wecom_utils.InboxStoreResult.STORED
    assert session.commits == 1
    assert [type(item) for item in session.added] == [
        WeComInbox,
        MessageMedia,
        MediaProcessingJob,
    ]
    inbox, media, job = session.added
    assert media.inbox_id == inbox.id
    assert job.media_id == media.id


@pytest.mark.asyncio
async def test_unsupported_media_has_no_download_job() -> None:
    session = RecordingSession()

    result = await wecom_utils.try_store_wecom_inbox(
        session,
        media_reference=WeComMediaReference(
            source_media_id="video-id",
            media_type="video",
            supported=False,
        ),
        platform_id=uuid4(),
        message_id="message-id",
        source_type="wecom_kf",
        from_user="external-user",
        msg_type="video",
        content="[video] video-id",
        status="unsupported_media",
    )

    assert result == wecom_utils.InboxStoreResult.STORED
    assert [type(item) for item in session.added] == [WeComInbox, MessageMedia]
    assert session.added[1].status == "unsupported"
