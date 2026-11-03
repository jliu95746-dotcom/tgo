"""Durable channel gauges survive worker restarts and expose no identifiers."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.db.base import get_db
from app.main import app


class DatabaseSession:
    def __init__(self, connection):
        self.connection = connection

    async def execute(self, statement):
        return self.connection.execute(statement)


@pytest.mark.asyncio
async def test_durable_metrics_count_only_wecom_customer_cases(monkeypatch):
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr("test-service-token")
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE TABLE pt_wecom_inbox (
              id VARCHAR PRIMARY KEY, platform_id VARCHAR, source_type VARCHAR,
              is_from_colleague BOOLEAN, status VARCHAR, lease_expires_at DATETIME,
              msg_type VARCHAR, failure_notice_status VARCHAR
            )
        """)
        )
        connection.execute(
            text("""
            CREATE TABLE pt_message_media (
              id VARCHAR PRIMARY KEY, inbox_id VARCHAR, platform_id VARCHAR,
              source_channel VARCHAR
            )
        """)
        )
        connection.execute(
            text("""
            CREATE TABLE pt_media_processing_jobs (
              id VARCHAR PRIMARY KEY, media_id VARCHAR, status VARCHAR,
              lease_expires_at DATETIME
            )
        """)
        )
        connection.execute(
            text("""
            INSERT INTO pt_wecom_inbox VALUES
              ('a','tenant-a','wecom_kf',0,'pending',NULL,'text',NULL),
              ('b','tenant-a','wecom_kf',0,'processing','2000-01-01','text',NULL),
              ('c','tenant-a','wecom_kf',0,'media_failed',NULL,'image','unknown'),
              ('d','tenant-a','wecom_kf',0,'completed',NULL,'text',NULL),
              ('e','tenant-a','wecom_kf',0,'new_future_status',NULL,'text',NULL),
              ('f','tenant-a','wecom_kf',0,'media_failed',NULL,'image','needs_attention'),
              ('bot','tenant-a','wecom_bot',0,'dead',NULL,'image','unknown'),
              ('staff','tenant-a','wecom_kf',1,'dead',NULL,'image','unknown')
        """)
        )
        connection.execute(
            text("""
            INSERT INTO pt_message_media VALUES
              ('media-a','a','tenant-a','wecom'),
              ('media-b','b','tenant-a','wecom'),
              ('media-bot','bot','tenant-a','wecom'),
              ('media-staff','staff','tenant-a','wecom'),
              ('media-mismatch','a','foreign-platform','wecom')
        """)
        )
        connection.execute(
            text("""
            INSERT INTO pt_media_processing_jobs VALUES
              ('job-a','media-a','pending',NULL),
              ('job-b','media-b','processing','2000-01-01'),
              ('job-bot','media-bot','dead',NULL),
              ('job-staff','media-staff','dead',NULL),
              ('job-mismatch','media-mismatch','dead',NULL)
        """)
        )

        async def fake_db():
            yield DatabaseSession(connection)

        app.dependency_overrides[get_db] = fake_db
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                denied = await client.get("/internal/metrics/durable")
                allowed = await client.get(
                    "/internal/metrics/durable",
                    headers={"X-SaaS-Service-Token": "test-service-token"},
                )
                denied_status = await client.get("/internal/wecom/durable-status")
                allowed_status = await client.get(
                    "/internal/wecom/durable-status",
                    headers={"X-SaaS-Service-Token": "test-service-token"},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()

    assert denied.status_code == 403
    assert denied_status.status_code == 403
    assert allowed.status_code == 200
    assert allowed_status.status_code == 200
    assert allowed_status.headers["cache-control"] == "no-store"
    assert allowed_status.json()["inbox"] == {
        "queued": 1,
        "processing": 1,
        "terminal_failure": 2,
        "completed": 1,
        "other": 1,
        "stale_lease": 1,
    }
    assert allowed_status.json()["media_jobs"] == {
        "queued": 1,
        "processing": 1,
        "terminal_failure": 0,
        "completed": 0,
        "other": 0,
        "stale_lease": 1,
    }
    assert allowed_status.json()["failure_notices"] == {
        "pending": 0,
        "sending": 0,
        "submitted": 0,
        "unknown": 1,
        "needs_attention": 1,
    }
    assert allowed.headers["content-type"].startswith("text/plain")
    assert 'tgo_platform_wecom_inbox_records{state="queued"} 1' in allowed.text
    assert 'tgo_platform_wecom_inbox_records{state="processing"} 1' in allowed.text
    assert (
        'tgo_platform_wecom_inbox_records{state="terminal_failure"} 2' in allowed.text
    )
    assert 'tgo_platform_wecom_inbox_records{state="other"} 1' in allowed.text
    assert "tgo_platform_wecom_inbox_stale_lease 1" in allowed.text
    assert 'tgo_platform_wecom_media_jobs{state="queued"} 1' in allowed.text
    assert 'tgo_platform_wecom_media_jobs{state="processing"} 1' in allowed.text
    assert "tgo_platform_wecom_media_stale_lease 1" in allowed.text
    assert 'tgo_platform_wecom_failure_notices{state="unknown"} 1' in allowed.text
    assert (
        'tgo_platform_wecom_failure_notices{state="needs_attention"} 1' in allowed.text
    )
    assert "tenant-a" not in allowed.text
    assert "tenant-a" not in allowed_status.text
    assert "foreign-platform" not in allowed.text
    assert "foreign-platform" not in allowed_status.text
    assert "test-service-token" not in allowed.text
    assert "test-service-token" not in allowed_status.text


@pytest.mark.asyncio
async def test_durable_metrics_database_failure_is_not_reported_as_zero(monkeypatch):
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr("test-service-token")
    )

    class FailedSession:
        async def execute(self, _statement):
            raise RuntimeError("database unavailable")

    async def fake_db():
        yield FailedSession()

    app.dependency_overrides[get_db] = fake_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/internal/metrics/durable",
                headers={"X-SaaS-Service-Token": "test-service-token"},
            )
            json_response = await client.get(
                "/internal/wecom/durable-status",
                headers={"X-SaaS-Service-Token": "test-service-token"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 500
    assert json_response.status_code == 500
    assert "tgo_platform_wecom_inbox_records" not in response.text


@pytest.mark.asyncio
async def test_failure_notice_query_failure_does_not_hide_alerts(monkeypatch):
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr("test-service-token")
    )

    class EmptyResult:
        def one(self):
            return (0, 0, 0, 0, 0, 0)

    class FailedNoticeSession:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            if self.calls % 3 == 0:
                raise RuntimeError("failure notice query unavailable")
            return EmptyResult()

    async def fake_db():
        yield FailedNoticeSession()

    app.dependency_overrides[get_db] = fake_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            for path in ("/internal/metrics/durable", "/internal/wecom/durable-status"):
                response = await client.get(
                    path,
                    headers={"X-SaaS-Service-Token": "test-service-token"},
                )
                assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_db, None)
