from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone

import pytest

from app.domain.services.listeners.wecom_failure_notice import WeComFailureNotifier
from app.domain.services.listeners.wecom_listener import WeComChannelListener


def platform() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        platform_type="wecom",
        cfg=SimpleNamespace(
            corp_id="corp",
            kf_secret="kf-secret",
            app_secret="",
            processing_batch_size=10,
        ),
    )


def inbox() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        platform_id=uuid4(),
        message_id="incoming-1",
        from_user="external-user",
        open_kfid="kf-account",
        msg_type="image",
        status="media_failed",
        source_type="wecom_kf",
        is_from_colleague=False,
        failure_notice_status="sending",
    )


@pytest.mark.asyncio
async def test_failed_media_submits_one_customer_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = WeComFailureNotifier(session_factory=None)  # type: ignore[arg-type]
    record = inbox()
    calls: list[tuple[str, str, str]] = []
    results: list[tuple[str, str | None]] = []

    async def claim(_: object) -> SimpleNamespace | None:
        return record if not results else None

    async def finish(_: object, status: str, error: str | None = None) -> None:
        results.append((status, error))

    class Adapter:
        def __init__(self, **kwargs: object) -> None:
            calls.append(
                (
                    str(kwargs["to_user"]),
                    str(kwargs["open_kfid"]),
                    str(kwargs["source_message_id"]),
                )
            )

        async def send_final(self, content: dict[str, str]) -> None:
            assert "重新发送" in content["text"]
            assert "人工" in content["text"]

    monkeypatch.setattr(notifier, "_claim", claim)
    monkeypatch.setattr(notifier, "_finish", finish)
    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_failure_notice.WeComAdapter", Adapter
    )

    await notifier.process_platform(platform())
    await notifier.process_platform(platform())

    assert len(calls) == 1
    assert calls[0] == (
        "external-user",
        "kf-account",
        f"media-fallback:{record.platform_id}:{record.message_id}",
    )
    assert results == [("submitted", None)]


@pytest.mark.asyncio
async def test_send_error_is_recorded_as_unknown_without_automatic_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = WeComFailureNotifier(session_factory=None)  # type: ignore[arg-type]
    record = inbox()
    outcomes: list[tuple[str, str | None]] = []
    send_attempts = 0

    async def claim(_: object) -> SimpleNamespace | None:
        return record if record.failure_notice_status == "sending" else None

    async def finish(_: object, status: str, error: str | None = None) -> None:
        outcomes.append((status, error))
        record.failure_notice_status = status

    class Adapter:
        def __init__(self, **_: object) -> None:
            pass

        async def send_final(self, _: dict[str, str]) -> None:
            nonlocal send_attempts
            send_attempts += 1
            raise TimeoutError("provider response unknown")

    monkeypatch.setattr(notifier, "_claim", claim)
    monkeypatch.setattr(notifier, "_finish", finish)
    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_failure_notice.WeComAdapter", Adapter
    )

    await notifier.process_platform(platform())
    await notifier.process_platform(platform())

    assert outcomes == [("unknown", "TimeoutError")]
    assert send_attempts == 1


@pytest.mark.asyncio
async def test_missing_recipient_is_flagged_for_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = WeComFailureNotifier(session_factory=None)  # type: ignore[arg-type]
    record = inbox()
    record.open_kfid = None
    outcomes: list[tuple[str, str | None]] = []

    async def claim(_: object) -> SimpleNamespace | None:
        return record if record.failure_notice_status == "sending" else None

    async def finish(_: object, status: str, error: str | None = None) -> None:
        outcomes.append((status, error))
        record.failure_notice_status = status

    monkeypatch.setattr(notifier, "_claim", claim)
    monkeypatch.setattr(notifier, "_finish", finish)

    await notifier.process_platform(platform())

    assert outcomes == [("needs_attention", "missing recipient or credentials")]


@pytest.mark.asyncio
async def test_failure_notifier_drains_multiple_records_up_to_batch_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = WeComFailureNotifier(session_factory=None)  # type: ignore[arg-type]
    records = [inbox(), inbox()]
    records[1].message_id = "incoming-2"
    submitted: list[str] = []

    async def claim(_: object) -> SimpleNamespace | None:
        return records.pop(0) if records else None

    async def finish(
        record: SimpleNamespace, status: str, error: str | None = None
    ) -> None:
        assert error is None
        assert status == "submitted"
        submitted.append(record.message_id)

    class Adapter:
        def __init__(self, **_: object) -> None:
            pass

        async def send_final(self, _: dict[str, str]) -> None:
            pass

    monkeypatch.setattr(notifier, "_claim", claim)
    monkeypatch.setattr(notifier, "_finish", finish)
    monkeypatch.setattr(
        "app.domain.services.listeners.wecom_failure_notice.WeComAdapter", Adapter
    )

    await notifier.process_platform(platform())

    assert submitted == ["incoming-1", "incoming-2"]


@pytest.mark.asyncio
async def test_interrupted_send_is_marked_unknown_without_resending() -> None:
    record = inbox()
    record.failure_notice_started_at = datetime.now(timezone.utc)
    record.failure_notice_finished_at = None
    record.failure_notice_error = None
    commits: list[str] = []

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def scalar(self, _: object) -> SimpleNamespace:
            return record

        async def commit(self) -> None:
            commits.append(record.failure_notice_status)

    notifier = WeComFailureNotifier(session_factory=lambda: Session())  # type: ignore[arg-type]

    assert await notifier._claim(platform()) is None
    assert record.failure_notice_status == "unknown"
    assert record.failure_notice_finished_at is not None
    assert commits == ["unknown"]


@pytest.mark.asyncio
async def test_wecom_listener_start_and_stop_with_failure_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = WeComChannelListener(
        session_factory=None,  # type: ignore[arg-type]
        normalizer=None,  # type: ignore[arg-type]
        tgo_api_client=None,  # type: ignore[arg-type]
        sse_manager=None,  # type: ignore[arg-type]
    )

    async def idle_loop() -> None:
        await listener._stop_event.wait()

    monkeypatch.setattr(listener, "_consumer_loop", idle_loop)
    await listener.start()
    task = listener._consumer_task
    assert task is not None and not task.done()
    await asyncio.sleep(0)
    await listener.stop()
    assert task.done()
    assert listener._consumer_task is None
