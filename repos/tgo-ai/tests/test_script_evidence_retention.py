"""The maintenance loop drains bounded batches and does not expose failures."""

import asyncio

import pytest

from app.tasks.script_evidence_retention import run_retention


@pytest.mark.asyncio
async def test_retention_drains_nonempty_batches_and_honors_stop():
    stop = asyncio.Event()
    results = iter([500, 1, 0])
    calls = []

    async def cleanup():
        count = next(results)
        calls.append(count)
        if count == 0:
            stop.set()
        return count

    await asyncio.wait_for(
        run_retention(
            stop, cleanup=cleanup, interval_seconds=0.01, drain_seconds=0.001
        ),
        0.5,
    )
    assert calls == [500, 1, 0]


@pytest.mark.asyncio
async def test_retention_recovers_without_logging_private_error_text(caplog):
    stop = asyncio.Event()
    calls = []

    async def cleanup():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("private-customer-fixture")
        stop.set()
        return 0

    await asyncio.wait_for(
        run_retention(stop, cleanup=cleanup, interval_seconds=0.001), 0.5
    )
    assert len(calls) == 2 and "private-customer-fixture" not in caplog.text


@pytest.mark.asyncio
async def test_stalled_retention_is_bounded_and_cleans_up():
    stop, released = asyncio.Event(), asyncio.Event()

    async def cleanup():
        try:
            await asyncio.Event().wait()
        finally:
            released.set()
            stop.set()
        return 0

    await asyncio.wait_for(
        run_retention(
            stop, cleanup=cleanup, interval_seconds=0.001, cleanup_timeout=0.01
        ),
        0.5,
    )
    assert released.is_set()


@pytest.mark.asyncio
async def test_application_lifespan_starts_and_joins_retention_before_db_close(
    monkeypatch,
):
    from app import main
    from app.tasks import embedding_sync_retry, script_evidence_retention

    started, closed = set(), []

    def worker(name):
        async def run(stop):
            started.add(name)
            try:
                await stop.wait()
            finally:
                closed.append(name)

        return run

    async def close_db():
        assert {"embedding", "retention"}.issubset(closed)
        closed.append("database")

    monkeypatch.setattr(
        embedding_sync_retry,
        "start_embedding_sync_retry_loop",
        worker("embedding"),
    )
    monkeypatch.setattr(
        script_evidence_retention, "run_retention", worker("retention")
    )
    monkeypatch.setattr(main, "close_db", close_db)
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert started == {"embedding", "retention"}
    assert closed[-1] == "database"
