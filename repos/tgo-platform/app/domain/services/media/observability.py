from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Protocol


class MediaFailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    VALIDATION = "validation"
    STORAGE = "storage"
    DATABASE = "database"
    CONCURRENCY = "concurrency"
    UNKNOWN = "unknown"


class MediaMetricEvent(StrEnum):
    JOB_STARTED = "job_started"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"
    JOB_RETRY_SCHEDULED = "job_retry_scheduled"
    JOB_TIMED_OUT = "job_timed_out"
    CLEANUP_SUCCEEDED = "cleanup_succeeded"
    CLEANUP_FAILED = "cleanup_failed"


class ObservedMediaType(StrEnum):
    IMAGE = "image"
    VOICE = "voice"
    UNKNOWN = "unknown"


class MediaCleanupKind(StrEnum):
    STAGING = "staging"
    RETENTION = "retention"
    CYCLE = "cycle"


@dataclass(frozen=True)
class MediaMetricDimensions:
    """A deliberately finite label set; identifiers and raw errors are excluded."""

    event: MediaMetricEvent
    media_type: ObservedMediaType | None = None
    failure_category: MediaFailureCategory | None = None
    cleanup_kind: MediaCleanupKind | None = None


@dataclass(frozen=True)
class MediaMetricPoint:
    dimensions: MediaMetricDimensions
    value: int


@dataclass(frozen=True)
class MediaMetricsSnapshot:
    counters: tuple[MediaMetricPoint, ...]
    in_flight: int
    peak_in_flight: int
    concurrency_limit: int

    def count(
        self,
        *,
        event: MediaMetricEvent | str,
        media_type: ObservedMediaType | str | None = None,
        failure_category: MediaFailureCategory | str | None = None,
        cleanup_kind: MediaCleanupKind | str | None = None,
    ) -> int:
        return sum(
            point.value
            for point in self.counters
            if point.dimensions.event == event
            and (media_type is None or point.dimensions.media_type == media_type)
            and (
                failure_category is None
                or point.dimensions.failure_category == failure_category
            )
            and (cleanup_kind is None or point.dimensions.cleanup_kind == cleanup_kind)
        )


class MediaMetricsSink(Protocol):
    def job_started(self, media_type: str) -> None: ...

    def job_succeeded(self, media_type: str) -> None: ...

    def job_failed(
        self,
        media_type: str,
        *,
        failure_category: MediaFailureCategory,
        retry_scheduled: bool,
        timed_out: bool,
    ) -> None: ...

    def job_finished(self) -> None: ...

    def cleanup_finished(
        self,
        cleanup_kind: MediaCleanupKind | str,
        *,
        succeeded: bool,
    ) -> None: ...

    def snapshot(self) -> MediaMetricsSnapshot: ...


class NoOpMediaMetrics:
    """Default sink for isolated listener use and backward compatibility."""

    def __init__(self, *, concurrency_limit: int = 1) -> None:
        self._concurrency_limit = concurrency_limit

    def job_started(self, media_type: str) -> None:
        return None

    def job_succeeded(self, media_type: str) -> None:
        return None

    def job_failed(
        self,
        media_type: str,
        *,
        failure_category: MediaFailureCategory,
        retry_scheduled: bool,
        timed_out: bool,
    ) -> None:
        return None

    def job_finished(self) -> None:
        return None

    def cleanup_finished(
        self,
        cleanup_kind: MediaCleanupKind | str,
        *,
        succeeded: bool,
    ) -> None:
        return None

    def snapshot(self) -> MediaMetricsSnapshot:
        return MediaMetricsSnapshot(
            counters=(),
            in_flight=0,
            peak_in_flight=0,
            concurrency_limit=self._concurrency_limit,
        )


class InMemoryMediaMetrics:
    """Bounded in-process metrics suitable for tests and backend adapters."""

    def __init__(self, *, concurrency_limit: int) -> None:
        if not 1 <= concurrency_limit <= 16:
            raise ValueError("media metrics concurrency limit must be between 1 and 16")
        self._concurrency_limit = concurrency_limit
        self._counters: Counter[MediaMetricDimensions] = Counter()
        self._in_flight = 0
        self._peak_in_flight = 0
        self._lock = RLock()

    def job_started(self, media_type: str) -> None:
        observed_type = _observed_media_type(media_type)
        with self._lock:
            self._increment(
                MediaMetricDimensions(
                    event=MediaMetricEvent.JOB_STARTED,
                    media_type=observed_type,
                )
            )
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)

    def job_succeeded(self, media_type: str) -> None:
        with self._lock:
            self._increment(
                MediaMetricDimensions(
                    event=MediaMetricEvent.JOB_SUCCEEDED,
                    media_type=_observed_media_type(media_type),
                )
            )

    def job_failed(
        self,
        media_type: str,
        *,
        failure_category: MediaFailureCategory,
        retry_scheduled: bool,
        timed_out: bool,
    ) -> None:
        observed_type = _observed_media_type(media_type)
        with self._lock:
            self._increment(
                MediaMetricDimensions(
                    event=MediaMetricEvent.JOB_FAILED,
                    media_type=observed_type,
                    failure_category=failure_category,
                )
            )
            if retry_scheduled:
                self._increment(
                    MediaMetricDimensions(
                        event=MediaMetricEvent.JOB_RETRY_SCHEDULED,
                        media_type=observed_type,
                    )
                )
            if timed_out:
                self._increment(
                    MediaMetricDimensions(
                        event=MediaMetricEvent.JOB_TIMED_OUT,
                        media_type=observed_type,
                    )
                )

    def job_finished(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def cleanup_finished(
        self,
        cleanup_kind: MediaCleanupKind | str,
        *,
        succeeded: bool,
    ) -> None:
        event = (
            MediaMetricEvent.CLEANUP_SUCCEEDED
            if succeeded
            else MediaMetricEvent.CLEANUP_FAILED
        )
        with self._lock:
            self._increment(
                MediaMetricDimensions(
                    event=event,
                    cleanup_kind=MediaCleanupKind(cleanup_kind),
                )
            )

    def snapshot(self) -> MediaMetricsSnapshot:
        with self._lock:
            points = tuple(
                MediaMetricPoint(dimensions=dimensions, value=value)
                for dimensions, value in sorted(
                    self._counters.items(),
                    key=lambda item: repr(item[0]),
                )
            )
            return MediaMetricsSnapshot(
                counters=points,
                in_flight=self._in_flight,
                peak_in_flight=self._peak_in_flight,
                concurrency_limit=self._concurrency_limit,
            )

    def _increment(self, dimensions: MediaMetricDimensions) -> None:
        self._counters[dimensions] += 1


def classify_media_failure(code: str) -> MediaFailureCategory:
    """Map provider/detail codes into a finite, non-sensitive metric category."""

    if code in {"processing_timeout", "http_408", "http_425"}:
        return MediaFailureCategory.TIMEOUT
    if code in {"http_429", "wecom_45009"}:
        return MediaFailureCategory.RATE_LIMITED
    if code == "platform_config_invalid":
        return MediaFailureCategory.CONFIGURATION
    if code in {
        "media_too_large",
        "empty_media",
        "unsupported_media_format",
        "image_dimensions_exceeded",
        "image_frame_limit_exceeded",
        "voice_duration_exceeded",
        "unexpected_partial_response",
        "error_payload_too_large",
        "invalid_error_payload",
    }:
        return MediaFailureCategory.VALIDATION
    if code == "database_finalize_error":
        return MediaFailureCategory.DATABASE
    if code in {"processing_error", "storage_delete_error"}:
        return MediaFailureCategory.STORAGE
    if code == "claim_lost":
        return MediaFailureCategory.CONCURRENCY
    if code in {"network_error", "http_error"} or code.startswith(("http_", "wecom_")):
        return MediaFailureCategory.UPSTREAM
    return MediaFailureCategory.UNKNOWN


def _observed_media_type(media_type: str) -> ObservedMediaType:
    if media_type == "image":
        return ObservedMediaType.IMAGE
    if media_type == "voice":
        return ObservedMediaType.VOICE
    return ObservedMediaType.UNKNOWN
