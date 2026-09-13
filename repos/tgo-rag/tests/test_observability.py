"""Dependency probes and monitoring must report measured state, not placeholders."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import CollectorRegistry
from sqlalchemy import create_engine, text

from src.rag_service.routers import health, monitoring
from src.rag_service.schemas.observability import DependencyCheck, HealthChecks
from src.rag_service.services import health_checks, knowledge_metrics
from src.rag_service.services.request_metrics import (
    RequestMetrics,
    RequestMetricsMiddleware,
)


def test_request_counters_start_empty_and_use_real_process_uptime():
    now = [10.0]
    metrics = RequestMetrics(CollectorRegistry(), clock=lambda: now[0])
    now[0] = 14.0
    result = metrics.snapshot()
    assert result.requests_total == 0 and result.uptime_seconds == 4.0
    assert result.response_time_p95 is None
    assert result.documents_processed is None and result.embeddings_generated is None
    assert result.active_connections is None


def test_latency_sample_memory_is_bounded():
    metrics = RequestMetrics(CollectorRegistry())
    for _ in range(1005):
        metrics.finish(metrics.begin(), False)
    assert metrics.snapshot().requests_total == 1005
    assert metrics.snapshot().latency_samples == 1000


@pytest.mark.asyncio
async def test_disabled_middleware_never_collects_requests():
    metrics = RequestMetrics(CollectorRegistry())
    app = AsyncMock()
    await RequestMetricsMiddleware(app, metrics=metrics, enabled=False)(
        {"type": "http", "path": "/sample"}, AsyncMock(), AsyncMock()
    )
    app.assert_awaited_once()
    assert metrics.snapshot().requests_total == 0


@pytest.mark.asyncio
async def test_actual_app_counts_cors_responses_too(monkeypatch):
    from src.rag_service import main
    from src.rag_service.services.request_metrics import request_metrics

    settings = main.get_settings()
    origin = "http://monitoring-test.invalid"
    monkeypatch.setattr(settings, "cors_origins", [origin])
    monkeypatch.setattr(settings, "metrics_enabled", True)
    before = request_metrics.snapshot().requests_total
    app = main.create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.options(
            "/", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
        )
    assert response.status_code == 200
    assert request_metrics.snapshot().requests_total == before + 1


@pytest.mark.asyncio
async def test_redis_health_really_pings_and_closes_the_connection(monkeypatch):
    client = AsyncMock()
    client.ping.return_value = True
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    monkeypatch.setattr(
        health_checks.Redis, "from_url", lambda *args, **kwargs: manager
    )
    await health_checks.check_redis()
    client.ping.assert_awaited_once()
    manager.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_tables_and_invalid_vector_operator_are_not_healthy(monkeypatch):
    class Session:
        async def execute(self, query):
            return SimpleNamespace(
                scalar_one=lambda: False if "to_regclass" in str(query) else 1.0
            )

    @asynccontextmanager
    async def session():
        yield Session()

    monkeypatch.setattr(health_checks, "get_db_session", session)
    assert (
        await health_checks.measure_probe(health_checks.check_database)
    ).status == "unhealthy"
    assert (
        await health_checks.measure_probe(health_checks.check_vector_database)
    ).status == "unhealthy"


@pytest.mark.asyncio
async def test_middleware_counts_real_requests_errors_and_excludes_probes():
    now = [1.0]
    metrics = RequestMetrics(CollectorRegistry(), clock=lambda: now[0])
    app = FastAPI()
    app.add_middleware(RequestMetricsMiddleware, metrics=metrics)

    @app.get("/sample")
    async def sample():
        assert metrics.snapshot().active_requests == 1
        now[0] += 0.2
        return {"ok": True}

    @app.get("/health")
    async def probe():
        return {"status": "healthy"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/sample")
        await client.get("/missing")
        await client.get("/health")
    result = metrics.snapshot()
    assert result.requests_total == 2 and result.errors_total == 1
    assert result.active_requests == 0 and result.latency_samples == 2
    assert result.response_time_p95 == pytest.approx(200.0)
    assert metrics.registry.get_sample_value("tgo_rag_http_requests_total") == 2


@pytest.mark.asyncio
async def test_streaming_request_is_observed_until_body_completion():
    now = [0.0]
    metrics = RequestMetrics(CollectorRegistry(), clock=lambda: now[0])

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        now[0] = 1.0
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        assert metrics.snapshot().active_requests == 1
        now[0] = 2.0
        await send({"type": "http.response.body", "body": b"last", "more_body": False})
        assert metrics.snapshot().active_requests == 0
        now[0] = 10.0  # Background work after response delivery is not HTTP latency.

    middleware = RequestMetricsMiddleware(app, metrics=metrics)
    await middleware({"type": "http", "path": "/sample"}, AsyncMock(), AsyncMock())
    assert metrics.snapshot().response_time_p95 == 2000.0
    assert metrics.snapshot().active_requests == 0


@pytest.mark.asyncio
async def test_failed_asgi_request_is_counted_once_and_releases_active_slot():
    metrics = RequestMetrics(CollectorRegistry())

    async def app(scope, receive, send):
        raise RuntimeError("failure")

    middleware = RequestMetricsMiddleware(app, metrics=metrics)
    with pytest.raises(RuntimeError):
        await middleware({"type": "http", "path": "/sample"}, AsyncMock(), AsyncMock())
    result = metrics.snapshot()
    assert result.requests_total == result.errors_total == 1
    assert result.active_requests == 0


@pytest.mark.asyncio
async def test_probe_timeout_and_errors_are_sanitized(monkeypatch):
    async def slow():
        await asyncio.sleep(1)

    result = await health_checks.measure_probe(slow, timeout=0.001)
    assert result.status == "unhealthy" and result.error == "TimeoutError"

    async def failed():
        raise RuntimeError("redis://secret:password@private-host")

    result = await health_checks.measure_probe(failed)
    assert result.error == "RuntimeError" and "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_health_and_ready_fail_if_any_required_dependency_fails(monkeypatch):
    good = DependencyCheck(status="healthy", response_time_ms=1)
    bad = DependencyCheck(
        status="unhealthy", response_time_ms=2, error="ConnectionError"
    )
    checks = HealthChecks(
        database=good, redis=bad, vector_db=good, total_check_time_ms=3
    )
    monkeypatch.setattr(health, "collect_health_checks", AsyncMock(return_value=checks))
    app = FastAPI()
    app.include_router(health.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 503 and response.json()["status"] == "unhealthy"
        assert (await client.get("/ready")).status_code == 503
        assert (await client.get("/live")).status_code == 200


@pytest.mark.asyncio
async def test_disabled_metrics_return_valid_explicit_state_without_db_queries(
    monkeypatch,
):
    monkeypatch.setattr(
        monitoring, "get_settings", lambda: SimpleNamespace(metrics_enabled=False)
    )
    collect = AsyncMock()
    monkeypatch.setattr(monitoring, "collect_knowledge_counts", collect)
    app = FastAPI()
    app.include_router(monitoring.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics/json")
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert response.json()["metrics"]["requests_total"] is None
    collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_counts_use_real_rows_and_exclude_soft_deleted_parents(
    monkeypatch,
):
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(
            text(
                "CREATE TABLE rag_collections "
                "(id TEXT, project_id TEXT, deleted_at TEXT)"
            )
        )
        db.execute(
            text(
                "CREATE TABLE rag_files (id TEXT, project_id TEXT, "
                "collection_id TEXT, deleted_at TEXT, status TEXT)"
            )
        )
        db.execute(
            text(
                "CREATE TABLE rag_file_documents (id TEXT, project_id TEXT, "
                "collection_id TEXT, file_id TEXT, embedding TEXT)"
            )
        )
        db.execute(
            text(
                "INSERT INTO rag_collections VALUES ('active', 'project', NULL), "
                "('deleted', 'project', 'gone')"
            )
        )
        db.execute(
            text(
                "INSERT INTO rag_files VALUES "
                "('ok', 'project', 'active', NULL, 'completed'), "
                "('pending', 'project', 'active', NULL, 'pending'), "
                "('removed', 'project', 'active', 'gone', 'completed'), "
                "('hidden', 'project', 'deleted', NULL, 'completed')"
            )
        )
        db.execute(
            text(
                "INSERT INTO rag_file_documents VALUES "
                "('1', 'project', 'active', 'ok', 'vector'), "
                "('2', 'project', 'active', NULL, NULL), "
                "('3', 'project', 'active', 'removed', 'vector'), "
                "('4', 'project', 'deleted', 'hidden', 'vector')"
            )
        )

        class Session:
            async def execute(self, query):
                return db.execute(query)

        @asynccontextmanager
        async def session():
            yield Session()

        monkeypatch.setattr(knowledge_metrics, "get_db_session", session)
        counts = await knowledge_metrics.collect_knowledge_counts()
    engine.dispose()
    assert counts.status == "available"
    assert counts.collections_total == 1 and counts.files_total == 2
    assert counts.files_completed == counts.files_pending == 1
    assert counts.documents_total == 2 and counts.embeddings_total == 1


@pytest.mark.asyncio
async def test_failed_counts_are_unavailable_not_zero(monkeypatch):
    @asynccontextmanager
    async def broken():
        raise RuntimeError("private database detail")
        yield

    monkeypatch.setattr(knowledge_metrics, "get_db_session", broken)
    counts = await knowledge_metrics.collect_knowledge_counts()
    assert counts.status == "unavailable" and counts.files_total is None
    assert counts.error == "RuntimeError" and "private" not in counts.model_dump_json()
