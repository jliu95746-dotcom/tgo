from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest

from app.domain.services.listeners.media_cleanup_listener import MediaCleanupListener
from app.domain.services.listeners.wecom_media_listener import WeComMediaListener, _ClaimedMediaJob
from app.domain.services.media.types import (
    DownloadedMedia,
    MediaDownloadError,
    StoredMediaObject,
)
from app.domain.services.media.observability import InMemoryMediaMetrics


def claimed_job() -> _ClaimedMediaJob:
    return _ClaimedMediaJob(
        job_id=uuid4(),
        media_id=uuid4(),
        inbox_id=uuid4(),
        platform_id=uuid4(),
        source_media_id="media-id",
        media_type="image",
        retry_count=0,
        max_attempts=3,
        claim_token="claim-token",
        previous_staging_object_key=None,
        corp_id="corp-id",
        app_secret="app-secret",
    )


class SuccessfulDownloader:
    async def download(self, **_: object) -> DownloadedMedia:
        return DownloadedMedia(
            content=b"\xff\xd8\xffimage",
            mime_type="image/jpeg",
            extension="jpg",
            sha256="a" * 64,
        )

    async def aclose(self) -> None:
        return None


class SuccessfulStorage:
    def object_key_for(self, **_: object) -> str:
        return "test/object.enc"

    async def put(self, **_: object) -> StoredMediaObject:
        return StoredMediaObject(
            provider="test",
            object_key="test/object.enc",
            encryption_mode="aes-256-gcm",
            encryption_key_id="test-key",
        )

    async def delete(self, **_: object) -> None:
        return None


@pytest.mark.asyncio
async def test_media_download_success_finalizes_without_text_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = InMemoryMediaMetrics(concurrency_limit=1)
    listener = WeComMediaListener(
        session_factory=None,  # type: ignore[arg-type]
        downloader=SuccessfulDownloader(),  # type: ignore[arg-type]
        storage=SuccessfulStorage(),
        metrics=metrics,
        max_concurrency=1,
    )
    finalized: list[str] = []

    async def fake_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_record_staging(*_: object) -> bool:
        return True

    async def fake_finalize(*_: object) -> bool:
        finalized.append("success")
        return True

    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_media_listener.wecom_get_access_token",
        fake_token,
    )
    monkeypatch.setattr(listener, "_record_staging_object", fake_record_staging)
    monkeypatch.setattr(listener, "_finalize_success", fake_finalize)

    await listener._process_claimed_job(claimed_job())

    assert finalized == ["success"]
    snapshot = metrics.snapshot()
    assert snapshot.count(event="job_succeeded", media_type="image") == 1
    assert snapshot.in_flight == 0


@pytest.mark.asyncio
async def test_permanent_download_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingDownloader(SuccessfulDownloader):
        async def download(self, **_: object) -> DownloadedMedia:
            raise MediaDownloadError(
                "wecom_40007",
                "invalid media id",
                retryable=False,
            )

    metrics = InMemoryMediaMetrics(concurrency_limit=1)
    listener = WeComMediaListener(
        session_factory=None,  # type: ignore[arg-type]
        downloader=FailingDownloader(),  # type: ignore[arg-type]
        storage=SuccessfulStorage(),
        metrics=metrics,
        max_concurrency=1,
    )
    failures: list[tuple[str, bool]] = []

    async def fake_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_failure(
        _: object,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        assert message == "invalid media id"
        failures.append((code, retryable))

    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_media_listener.wecom_get_access_token",
        fake_token,
    )
    monkeypatch.setattr(listener, "_finalize_failure", fake_failure)

    await listener._process_claimed_job(claimed_job())

    assert failures == [("wecom_40007", False)]
    snapshot = metrics.snapshot()
    assert (
        snapshot.count(
            event="job_failed",
            media_type="image",
            failure_category="upstream",
        )
        == 1
    )
    assert snapshot.count(event="job_retry_scheduled") == 0


@pytest.mark.asyncio
async def test_timeout_is_counted_and_retry_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimedOutDownloader(SuccessfulDownloader):
        async def download(self, **_: object) -> DownloadedMedia:
            raise TimeoutError("access-token media-id must never reach logs")

    metrics = InMemoryMediaMetrics(concurrency_limit=1)
    listener = WeComMediaListener(
        session_factory=None,  # type: ignore[arg-type]
        downloader=TimedOutDownloader(),  # type: ignore[arg-type]
        storage=SuccessfulStorage(),
        metrics=metrics,
        max_concurrency=1,
    )
    failures: list[tuple[str, bool]] = []

    async def fake_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_failure(
        _: object,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        failures.append((code, retryable))

    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_media_listener.wecom_get_access_token",
        fake_token,
    )
    monkeypatch.setattr(listener, "_finalize_failure", fake_failure)

    await listener._process_claimed_job(claimed_job())

    assert failures == [("processing_timeout", True)]
    snapshot = metrics.snapshot()
    assert snapshot.count(event="job_timed_out", media_type="image") == 1
    assert snapshot.count(event="job_retry_scheduled", media_type="image") == 1


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_log_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    forbidden_values = (
        "corp-secret",
        "access-token",
        "source-media-id",
        "customer-id",
        "test/private/object.enc",
        "media-plaintext",
    )

    class SensitiveFailureStorage(SuccessfulStorage):
        async def put(self, **_: object) -> StoredMediaObject:
            raise RuntimeError(" ".join(forbidden_values))

    metrics = InMemoryMediaMetrics(concurrency_limit=1)
    listener = WeComMediaListener(
        session_factory=None,  # type: ignore[arg-type]
        downloader=SuccessfulDownloader(),  # type: ignore[arg-type]
        storage=SensitiveFailureStorage(),
        metrics=metrics,
        max_concurrency=1,
    )
    claimed = claimed_job()
    claimed = _ClaimedMediaJob(
        **{
            **claimed.__dict__,
            "source_media_id": "source-media-id",
            "corp_id": "customer-id",
            "app_secret": "corp-secret",
        }
    )

    async def fake_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_staging(*_: object) -> bool:
        return True

    async def fake_failure(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_media_listener.wecom_get_access_token",
        fake_token,
    )
    monkeypatch.setattr(listener, "_record_staging_object", fake_staging)
    monkeypatch.setattr(listener, "_finalize_failure", fake_failure)

    with caplog.at_level(logging.ERROR):
        await listener._process_claimed_job(claimed)

    logged = caplog.text
    assert "error_type=RuntimeError" in logged
    for forbidden in forbidden_values:
        assert forbidden not in logged


@pytest.mark.asyncio
async def test_media_processing_respects_explicit_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConcurrencyProbeDownloader(SuccessfulDownloader):
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def download(self, **_: object) -> DownloadedMedia:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return await super().download()

    downloader = ConcurrencyProbeDownloader()
    metrics = InMemoryMediaMetrics(concurrency_limit=1)
    listener = WeComMediaListener(
        session_factory=None,  # type: ignore[arg-type]
        downloader=downloader,  # type: ignore[arg-type]
        storage=SuccessfulStorage(),
        metrics=metrics,
        max_concurrency=1,
    )

    async def fake_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_record_staging(*_: object) -> bool:
        return True

    async def fake_finalize(*_: object) -> bool:
        return True

    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_media_listener.wecom_get_access_token",
        fake_token,
    )
    monkeypatch.setattr(listener, "_record_staging_object", fake_record_staging)
    monkeypatch.setattr(listener, "_finalize_success", fake_finalize)

    await asyncio.gather(*(listener._process_claimed_job(claimed_job()) for _ in range(3)))

    assert downloader.peak == 1
    assert metrics.snapshot().peak_in_flight == 1


@pytest.mark.asyncio
async def test_cleanup_cycle_metrics_and_logs_are_identifier_free(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics = InMemoryMediaMetrics(concurrency_limit=1)
    cleanup = MediaCleanupListener(
        session_factory=None,  # type: ignore[arg-type]
        object_deleter=SuccessfulStorage(),
        metrics=metrics,
    )
    forbidden_values = "customer-id access-token test/private/object.enc"

    async def failing_cleanup() -> None:
        raise RuntimeError(forbidden_values)

    async def stop_after_cycle(_: float) -> None:
        cleanup._stop_event.set()

    monkeypatch.setattr(cleanup, "_cleanup_staging_objects", failing_cleanup)
    monkeypatch.setattr(
        "app.domain.services.listeners.media_cleanup_listener.asyncio.sleep",
        stop_after_cycle,
    )

    with caplog.at_level(logging.ERROR):
        await cleanup._consumer_loop()

    snapshot = metrics.snapshot()
    assert snapshot.count(event="cleanup_failed", cleanup_kind="cycle") == 1
    assert "error_type=RuntimeError" in caplog.text
    for forbidden in forbidden_values.split():
        assert forbidden not in caplog.text


@pytest.mark.asyncio
async def test_previous_staging_object_is_deleted_before_new_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []

    class RecordingStorage(SuccessfulStorage):
        async def delete(self, *, object_key: str) -> None:
            deleted.append(object_key)

    listener = WeComMediaListener(
        session_factory=None,  # type: ignore[arg-type]
        downloader=SuccessfulDownloader(),  # type: ignore[arg-type]
        storage=RecordingStorage(),
    )
    claimed = claimed_job()
    claimed = _ClaimedMediaJob(
        **{
            **claimed.__dict__,
            "previous_staging_object_key": "test/old-attempt.enc",
        }
    )

    async def fake_clear(*_: object) -> bool:
        return True

    async def stop_after_cleanup(*_: object) -> bool:
        return False

    async def fake_token(*_: object, **__: object) -> str:
        return "access-token"

    monkeypatch.setattr(listener, "_clear_previous_staging", fake_clear)
    monkeypatch.setattr(listener, "_record_staging_object", stop_after_cleanup)
    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_media_listener.wecom_get_access_token",
        fake_token,
    )

    await listener._process_claimed_job(claimed)

    assert deleted == ["test/old-attempt.enc"]


def test_cleanup_listener_is_independent_from_download_listener() -> None:
    cleanup = MediaCleanupListener(
        session_factory=None,  # type: ignore[arg-type]
        object_deleter=SuccessfulStorage(),
    )

    assert cleanup is not None
