"""Verify native RAG monitoring with owned metadata fixtures and no model calls."""

import argparse
import asyncio
import secrets
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos" / "tgo-rag"))

from src.rag_service.config import get_settings  # noqa: E402
from src.rag_service.database import close_database, get_db_session  # noqa: E402
from src.rag_service.models import Collection, File, FileDocument, Project  # noqa: E402
from src.rag_service.routers.health import router as health_router  # noqa: E402


async def verify(rag_base: str) -> None:
    # Disable SQL parameter echo only in this disposable verifier process.
    get_settings().debug = False
    marker = "rag-metrics-e2e-" + uuid4().hex[:12]
    print(f"Isolated fixture: {marker}")
    project_id, collection_id, file_id, document_id = [uuid4() for _ in range(4)]
    async with httpx.AsyncClient(base_url=rag_base, timeout=15) as client:
        health = await client.get("/health")
        assert health.status_code == 200 and health.json()["status"] == "healthy"
        before_response = await client.get("/metrics/json")
        assert before_response.status_code == 200
        before = before_response.json()
        assert before["enabled"] and before["knowledge"]["status"] == "available"
        assert before["scope"] == "current_api_worker"
        assert before["metrics"]["embeddings_generated"] is None
        try:
            async with get_db_session() as db:
                db.add(
                    Project(
                        id=project_id, name=marker, api_key=secrets.token_urlsafe(24)
                    )
                )
                db.add(
                    Collection(
                        id=collection_id, project_id=project_id, display_name=marker
                    )
                )
                await db.flush()
                db.add(
                    File(
                        id=file_id,
                        project_id=project_id,
                        collection_id=collection_id,
                        original_filename=f"{marker}.txt",
                        file_size=0,
                        content_type="text/plain",
                        storage_provider="local",
                        storage_path=f"fixture-only/{marker}.txt",
                        status="completed",
                        document_count=1,
                    )
                )
                await db.flush()
                db.add(
                    FileDocument(
                        id=document_id,
                        project_id=project_id,
                        collection_id=collection_id,
                        file_id=file_id,
                        content=marker,
                        content_length=len(marker),
                        embedding=[1.0] + [0.0] * 1535,
                    )
                )
            after = (await client.get("/metrics/json")).json()
            for field in (
                "collections_total",
                "files_total",
                "files_completed",
                "documents_total",
                "embeddings_total",
            ):
                assert (
                    after["knowledge"][field] == before["knowledge"][field] + 1
                ), field
            for _ in range(3):
                assert (await client.get("/")).status_code == 200
            assert (await client.get(f"/not-found-{marker}")).status_code == 404
            measured = (await client.get("/metrics/json")).json()["metrics"]
            assert measured["requests_total"] >= after["metrics"]["requests_total"] + 4
            assert measured["errors_total"] >= after["metrics"]["errors_total"] + 1
            assert measured["active_requests"] == 0
            assert measured["response_time_p95"] is not None
            preflight = await client.options(
                "/",
                headers={
                    "Origin": "http://monitoring-test.invalid",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert preflight.status_code in (200, 400)
            after_cors = (await client.get("/metrics/json")).json()["metrics"]
            assert after_cors["requests_total"] >= measured["requests_total"] + 1
            prometheus = await client.get("/metrics")
            assert prometheus.status_code == 200
            assert "tgo_rag_http_requests_total" in prometheus.text
            print("PASS: real DB counts changed by the exact owned fixture delta")
            print(
                "PASS: live HTTP counters/errors/latencies and Prometheus observations"
            )
        finally:
            async with get_db_session() as db:
                project = await db.get(Project, project_id)
                collection = await db.get(Collection, collection_id)
                file = await db.get(File, file_id)
                document = await db.get(FileDocument, document_id)
                assert project is None or project.name == marker
                assert collection is None or (
                    collection.project_id == project_id
                    and collection.display_name == marker
                )
                assert file is None or (
                    file.project_id == project_id
                    and file.original_filename == f"{marker}.txt"
                )
                assert document is None or (
                    document.project_id == project_id and document.content == marker
                )
                await db.execute(
                    delete(FileDocument).where(FileDocument.id == document_id)
                )
                await db.execute(delete(File).where(File.id == file_id))
                await db.execute(
                    delete(Collection).where(Collection.id == collection_id)
                )
                await db.execute(delete(Project).where(Project.id == project_id))
            print("CLEANUP: removed only owned project, collection, file and document")
        restored = (await client.get("/metrics/json")).json()["knowledge"]
        for field in (
            "collections_total",
            "files_total",
            "documents_total",
            "embeddings_total",
        ):
            assert restored[field] == before["knowledge"][field], field

    # Only this helper sees the injected fault; the running server is unchanged.
    settings = get_settings()
    original_redis_url = settings.redis_url
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))  # Bound, but deliberately not listening.
        settings.redis_url = f"redis://127.0.0.1:{reserved.getsockname()[1]}/0"
        try:
            app = FastAPI()
            app.include_router(health_router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://local-probe"
            ) as fault_client:
                failed = await fault_client.get("/health")
                assert failed.status_code == 503
                assert failed.json()["checks"]["redis"]["status"] == "unhealthy"
                assert (await fault_client.get("/ready")).status_code == 503
                assert (await fault_client.get("/live")).status_code == 200
        finally:
            settings.redis_url = original_redis_url
    print("PASS: actual refused Redis connection yields HTTP503; liveness remains200")
    await close_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rag-base", required=True)
    args = parser.parse_args()
    if urlparse(args.rag_base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This fixture is restricted to a local native RAG service")
    asyncio.run(verify(args.rag_base))
