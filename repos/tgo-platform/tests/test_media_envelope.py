"""Channel-neutral media envelope contracts."""

from __future__ import annotations

from uuid import uuid4

from app.domain.services.media.ingestion import build_media_records
from app.domain.services.media.types import (
    ChannelMediaReference,
    MediaEnvelope,
)


def test_media_envelope_builds_records_without_wecom_protocol_fields() -> None:
    envelope = MediaEnvelope(
        platform_id=uuid4(),
        source_channel="telegram",
        source_inbox_id=uuid4(),
        source_message_id="telegram:chat-1:message-9",
        reference=ChannelMediaReference(
            source_media_id="file-123",
            media_type="image",
            supported=True,
            original_filename="photo.jpg",
        ),
    )

    media, job = build_media_records(envelope, max_attempts=4)

    assert media.source_channel == "telegram"
    assert media.source_message_id == envelope.source_message_id
    assert media.inbox_id == envelope.source_inbox_id
    assert job is not None
    assert job.media_id == media.id
    assert job.max_attempts == 4
