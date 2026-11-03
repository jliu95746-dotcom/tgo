"""Authenticated operational metrics endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.billing_receipts import require_service
from app.db.base import get_db
from app.db.models import MediaProcessingJob, MessageMedia, WeComInbox
from app.domain.services.media.observability import MediaMetricsSnapshot


router = APIRouter(prefix="/internal", dependencies=[Depends(require_service)])

_INBOX_QUEUED = ("pending", "failed", "pending_media", "media_downloaded")
_INBOX_PROCESSING = ("processing", "media_downloading")
_INBOX_TERMINAL = ("dead", "media_failed", "unsupported_media")
_INBOX_COMPLETED = ("completed", "completed_no_reply")
_MEDIA_QUEUED = ("pending", "failed")


class WeComFailureNoticeCounts(BaseModel):
    pending: int = 0
    sending: int = 0
    submitted: int = 0
    unknown: int = 0
    needs_attention: int = 0


class DurableCounts(BaseModel):
    queued: int
    processing: int
    terminal_failure: int
    completed: int
    other: int
    stale_lease: int


class WeComDurableStatus(BaseModel):
    checked_at: datetime
    inbox: DurableCounts
    media_jobs: DurableCounts
    failure_notices: WeComFailureNoticeCounts


@router.get("/wecom/media-failure-notices", response_model=WeComFailureNoticeCounts)
async def wecom_media_failure_notices(
    db: AsyncSession = Depends(get_db),
) -> WeComFailureNoticeCounts:
    """Return aggregate fallback states to internal monitoring, without customer IDs."""
    return await read_failure_notice_counts(db)


async def read_failure_notice_counts(
    db: AsyncSession,
) -> WeComFailureNoticeCounts:
    rows = (
        await db.execute(
            select(WeComInbox.failure_notice_status, func.count())
            .where(
                WeComInbox.source_type == "wecom_kf",
                WeComInbox.is_from_colleague.is_(False),
                or_(
                    WeComInbox.status.in_(["media_failed", "unsupported_media"]),
                    and_(
                        WeComInbox.status == "dead",
                        WeComInbox.msg_type.in_(["image", "voice"]),
                        WeComInbox.failure_notice_status == "needs_attention",
                    ),
                ),
            )
            .group_by(WeComInbox.failure_notice_status)
        )
    ).all()
    counts = WeComFailureNoticeCounts()
    for status, count in rows:
        name = status or "pending"
        if name in WeComFailureNoticeCounts.model_fields:
            setattr(counts, name, int(count))
    return counts


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    output = request.app.state.callback_metrics.render_prometheus()
    media_metrics = getattr(request.app.state, "media_metrics", None)
    if media_metrics is not None:
        output += render_media_metrics(media_metrics.snapshot())
    return PlainTextResponse(
        output,
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/metrics/durable", response_class=PlainTextResponse)
async def durable_metrics(db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    """Expose current stored WeCom KF work across workers without identifiers."""
    inbox, media = await read_durable_counts(db)
    notices = await read_failure_notice_counts(db)
    output = render_durable_metrics(inbox, media, notices)
    return PlainTextResponse(
        output,
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/wecom/durable-status", response_model=WeComDurableStatus)
async def wecom_durable_status(
    response: Response, db: AsyncSession = Depends(get_db)
) -> WeComDurableStatus:
    """Provide the same stored work snapshot to the authorized operator console."""
    response.headers["Cache-Control"] = "no-store"
    inbox, media = await read_durable_counts(db)
    notices = await read_failure_notice_counts(db)
    return WeComDurableStatus(
        checked_at=datetime.now(timezone.utc),
        inbox=as_durable_counts(inbox),
        media_jobs=as_durable_counts(media),
        failure_notices=notices,
    )


def as_durable_counts(row: tuple[int, ...]) -> DurableCounts:
    return DurableCounts(
        queued=row[1],
        processing=row[2],
        terminal_failure=row[3],
        completed=row[4],
        other=max(0, row[0] - sum(row[1:5])),
        stale_lease=row[5],
    )


async def read_durable_counts(
    db: AsyncSession,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    now = datetime.now(timezone.utc)
    inbox = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(WeComInbox.status.in_(_INBOX_QUEUED)),
                func.count().filter(WeComInbox.status.in_(_INBOX_PROCESSING)),
                func.count().filter(WeComInbox.status.in_(_INBOX_TERMINAL)),
                func.count().filter(WeComInbox.status.in_(_INBOX_COMPLETED)),
                func.count().filter(
                    WeComInbox.status.in_(_INBOX_PROCESSING),
                    WeComInbox.lease_expires_at < now,
                ),
            ).where(
                WeComInbox.source_type == "wecom_kf",
                WeComInbox.is_from_colleague.is_(False),
            )
        )
    ).one()
    media = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(MediaProcessingJob.status.in_(_MEDIA_QUEUED)),
                func.count().filter(MediaProcessingJob.status == "processing"),
                func.count().filter(MediaProcessingJob.status == "dead"),
                func.count().filter(MediaProcessingJob.status == "completed"),
                func.count().filter(
                    MediaProcessingJob.status == "processing",
                    MediaProcessingJob.lease_expires_at < now,
                ),
            )
            .select_from(MediaProcessingJob)
            .join(MessageMedia, MessageMedia.id == MediaProcessingJob.media_id)
            .join(
                WeComInbox,
                and_(
                    WeComInbox.id == MessageMedia.inbox_id,
                    WeComInbox.platform_id == MessageMedia.platform_id,
                ),
            )
            .where(
                WeComInbox.source_type == "wecom_kf",
                WeComInbox.is_from_colleague.is_(False),
                MessageMedia.source_channel == "wecom",
            )
        )
    ).one()
    return (
        tuple(int(value) for value in inbox),
        tuple(int(value) for value in media),
    )


def render_durable_metrics(
    inbox: tuple[int, ...],
    media: tuple[int, ...],
    notices: WeComFailureNoticeCounts,
) -> str:
    """Use a fixed label set; never include platform, customer or message IDs."""
    lines = [
        "# HELP tgo_platform_wecom_inbox_records Current stored customer-service messages.",
        "# TYPE tgo_platform_wecom_inbox_records gauge",
    ]
    for state, count in zip(
        ("queued", "processing", "terminal_failure", "completed", "other"),
        (*inbox[1:5], max(0, inbox[0] - sum(inbox[1:5]))),
    ):
        lines.append(f'tgo_platform_wecom_inbox_records{{state="{state}"}} {count}')
    lines.extend(
        (
            "# TYPE tgo_platform_wecom_inbox_stale_lease gauge",
            f"tgo_platform_wecom_inbox_stale_lease {inbox[5]}",
            "# HELP tgo_platform_wecom_media_jobs Current stored customer-service media jobs.",
            "# TYPE tgo_platform_wecom_media_jobs gauge",
        )
    )
    for state, count in zip(
        ("queued", "processing", "terminal_failure", "completed", "other"),
        (*media[1:5], max(0, media[0] - sum(media[1:5]))),
    ):
        lines.append(f'tgo_platform_wecom_media_jobs{{state="{state}"}} {count}')
    lines.extend(
        (
            "# TYPE tgo_platform_wecom_media_stale_lease gauge",
            f"tgo_platform_wecom_media_stale_lease {media[5]}",
            "# HELP tgo_platform_wecom_failure_notices Current media failure fallback state.",
            "# TYPE tgo_platform_wecom_failure_notices gauge",
        )
    )
    for state, count in (
        ("pending", notices.pending),
        ("sending", notices.sending),
        ("submitted", notices.submitted),
        ("unknown", notices.unknown),
        ("needs_attention", notices.needs_attention),
    ):
        lines.append(f'tgo_platform_wecom_failure_notices{{state="{state}"}} {count}')
    return "\n".join(lines) + "\n"


def render_media_metrics(snapshot: MediaMetricsSnapshot) -> str:
    """Export finite media event labels and process-local concurrency gauges."""
    lines = [
        "# HELP tgo_platform_media_events_total Media job and cleanup events.",
        "# TYPE tgo_platform_media_events_total counter",
    ]
    for point in snapshot.counters:
        dimensions = point.dimensions
        labels = (
            f'event="{dimensions.event.value}",'
            f'media_type="{dimensions.media_type.value if dimensions.media_type else "none"}",'
            f'failure_category="{dimensions.failure_category.value if dimensions.failure_category else "none"}",'
            f'cleanup_kind="{dimensions.cleanup_kind.value if dimensions.cleanup_kind else "none"}"'
        )
        lines.append(f"tgo_platform_media_events_total{{{labels}}} {point.value}")
    for name, value in (
        ("in_flight", snapshot.in_flight),
        ("peak_in_flight", snapshot.peak_in_flight),
        ("concurrency_limit", snapshot.concurrency_limit),
    ):
        metric_name = f"tgo_platform_media_{name}"
        lines.extend(
            [
                f"# TYPE {metric_name} gauge",
                f"{metric_name} {value}",
            ]
        )
    return "\n".join(lines) + "\n"
