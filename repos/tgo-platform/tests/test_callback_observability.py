from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.api.v1.callbacks import compute_msg_signature
from app.core.config import settings
from app.db.base import get_db
from app.domain.services.callback_observability import CallbackMetrics
from app.domain.services.media.observability import InMemoryMediaMetrics
from app.main import app


def test_callback_histogram_has_bounded_labels_and_cumulative_buckets() -> None:
    metrics = CallbackMetrics()
    metrics.observe(channel="wecom", status_code=200, duration_seconds=0.12)
    metrics.observe(channel="wecom", status_code=500, duration_seconds=0.8)
    metrics.observe(channel="private-api-key", status_code=403, duration_seconds=0.02)

    output = metrics.render_prometheus()

    assert (
        "tgo_platform_callback_duration_seconds_bucket"
        '{channel="wecom",outcome="success",le="0.5"} 1'
    ) in output
    assert (
        "tgo_platform_callback_duration_seconds_bucket"
        '{channel="wecom",outcome="server_error",le="0.5"} 0'
    ) in output
    assert (
        "tgo_platform_callback_duration_seconds_bucket"
        '{channel="wecom",outcome="server_error",le="+Inf"} 1'
    ) in output
    assert 'channel="other",outcome="client_error"' in output
    assert "private-api-key" not in output


@pytest.mark.asyncio
async def test_metrics_endpoint_requires_internal_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "saas_internal_token", SecretStr("local-test-token"))
    metrics = CallbackMetrics()
    metrics.observe(channel="wecom", status_code=200, duration_seconds=0.1)
    monkeypatch.setattr(app.state, "callback_metrics", metrics)
    media_metrics = InMemoryMediaMetrics(concurrency_limit=2)
    media_metrics.job_started("image")
    media_metrics.job_succeeded("image")
    media_metrics.job_finished()
    monkeypatch.setattr(app.state, "media_metrics", media_metrics, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.get("/internal/metrics")
        allowed = await client.get(
            "/internal/metrics",
            headers={"X-SaaS-Service-Token": "local-test-token"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.headers["content-type"].startswith("text/plain")
    assert "tgo_platform_callback_duration_seconds_bucket" in allowed.text
    assert (
        'tgo_platform_media_events_total{event="job_succeeded",'
        'media_type="image",failure_category="none",cleanup_kind="none"} 1'
    ) in allowed.text
    assert "tgo_platform_media_in_flight 0" in allowed.text
    assert "local-test-token" not in allowed.text


@pytest.mark.asyncio
async def test_unknown_callback_does_not_log_the_platform_api_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeSession:
        async def scalar(self, _statement: object) -> None:
            return None

    async def fake_db():
        yield FakeSession()

    app.dependency_overrides[get_db] = fake_db
    try:
        with caplog.at_level(logging.WARNING):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/v1/platforms/callback/private-api-key")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
    assert "private-api-key" not in caplog.text


@pytest.mark.asyncio
async def test_wecom_callback_records_latency_without_exposing_api_key() -> None:
    metrics = CallbackMetrics()
    previous_metrics = app.state.callback_metrics
    app.state.callback_metrics = metrics
    platform = SimpleNamespace(
        id=uuid4(),
        type="wecom",
        config={"token": "local-signing-token"},
    )

    class FakeSession:
        async def scalar(self, _statement: object) -> SimpleNamespace:
            return platform

    async def fake_db():
        yield FakeSession()

    app.dependency_overrides[get_db] = fake_db
    try:
        signature = compute_msg_signature("local-signing-token", "1710000000", "nonce")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/platforms/callback/private-api-key",
                params={
                    "msg_signature": signature,
                    "timestamp": "1710000000",
                    "nonce": "nonce",
                },
                content=(
                    "<xml><MsgType>event</MsgType><Event>ignored-event</Event></xml>"
                ),
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.callback_metrics = previous_metrics

    assert response.status_code == 200
    output = metrics.render_prometheus()
    assert 'channel="wecom",outcome="success",le="+Inf"} 1' in output
    assert "private-api-key" not in output
    assert "local-signing-token" not in output
