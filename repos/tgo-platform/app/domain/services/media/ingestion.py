"""Build durable media metadata from a channel-neutral media envelope."""

from __future__ import annotations

import uuid

from app.db.models import MediaProcessingJob, MessageMedia
from app.domain.services.media.types import MediaEnvelope


def build_media_records(
    envelope: MediaEnvelope,
    *,
    max_attempts: int,
) -> tuple[MessageMedia, MediaProcessingJob | None]:
    """Create channel-neutral media metadata and an optional download job."""

    media_id = uuid.uuid4()
    reference = envelope.reference
    media = MessageMedia(
        id=media_id,
        platform_id=envelope.platform_id,
        inbox_id=envelope.source_inbox_id,
        source_channel=envelope.source_channel,
        source_message_id=envelope.source_message_id,
        source_media_id=reference.source_media_id,
        media_type=reference.media_type,
        status="pending" if reference.supported else "unsupported",
        original_filename=reference.original_filename,
        declared_size=reference.declared_size,
    )
    if not reference.supported:
        return media, None
    return (
        media,
        MediaProcessingJob(
            id=uuid.uuid4(),
            media_id=media_id,
            job_type="download",
            status="pending",
            max_attempts=max_attempts,
        ),
    )
