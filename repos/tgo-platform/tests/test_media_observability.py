from __future__ import annotations

from app.domain.services.media.observability import (
    InMemoryMediaMetrics,
    classify_media_failure,
)


def test_failure_codes_are_mapped_to_bounded_categories() -> None:
    assert classify_media_failure("processing_timeout") == "timeout"
    assert classify_media_failure("http_429") == "rate_limited"
    assert classify_media_failure("wecom_45009") == "rate_limited"
    assert classify_media_failure("platform_config_invalid") == "configuration"
    assert classify_media_failure("media_too_large") == "validation"
    assert classify_media_failure("database_finalize_error") == "database"
    assert classify_media_failure("unrecognized-provider-code-123") == "unknown"


def test_in_memory_snapshot_contains_only_low_cardinality_dimensions() -> None:
    metrics = InMemoryMediaMetrics(concurrency_limit=2)

    metrics.job_started("image")
    metrics.job_failed(
        "image",
        failure_category=classify_media_failure("unbounded-secret-bearing-code"),
        retry_scheduled=True,
        timed_out=False,
    )
    metrics.job_finished()
    metrics.cleanup_finished("staging", succeeded=True)

    snapshot = metrics.snapshot()

    assert snapshot.in_flight == 0
    assert snapshot.peak_in_flight == 1
    assert snapshot.concurrency_limit == 2
    assert snapshot.count(event="job_started", media_type="image") == 1
    assert (
        snapshot.count(
            event="job_failed",
            media_type="image",
            failure_category="unknown",
        )
        == 1
    )
    assert snapshot.count(event="job_retry_scheduled", media_type="image") == 1
    assert snapshot.count(event="cleanup_succeeded", cleanup_kind="staging") == 1
    assert "unbounded-secret-bearing-code" not in repr(snapshot)
