"""Check private paths and tenant filters through real HTTP routes."""

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.rag_service.database import get_db_session_dependency
from src.rag_service.models import Collection, File
from src.rag_service.routers import collections, files


def file_app(db):
    async def database():
        yield db

    app = FastAPI()
    app.include_router(files.router, prefix="/v1/files")
    app.include_router(collections.router, prefix="/v1/collections")
    app.dependency_overrides[get_db_session_dependency] = database
    return app


async def assert_collection_isolation(client, company, own, other):
    params = {"project_id": str(company)}
    listing = await client.get("/v1/collections", params=params)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["data"]] == [str(own)]
    detail = await client.get(f"/v1/collections/{own}", params=params)
    assert detail.status_code == 200, detail.text
    batch = await client.post(
        "/v1/collections/batch",
        params=params,
        json={"collection_ids": [str(own), str(other)]},
    )
    assert batch.status_code == 200, batch.text
    assert [item["id"] for item in batch.json()["collections"]] == [str(own)]
    assert batch.json()["not_found"] == [str(other)]
    for method, suffix, body in [
        ("GET", "", None),
        ("GET", "/documents", None),
        ("GET", "/pages", None),
        ("DELETE", "", None),
        ("PUT", "", {"display_name": "forbidden rename"}),
        ("POST", "/documents/search", {"query": "private document"}),
    ]:
        denied = await client.request(
            method,
            f"/v1/collections/{other}{suffix}",
            params=params,
            json=body,
        )
        assert denied.status_code == 404, denied.text
    soft_delete = await client.delete(
        f"/v1/collections/{other}",
        params={**params, "hard_delete": "false"},
    )
    assert soft_delete.status_code == 404, soft_delete.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", ["inside", "sibling", "traversal", "outside"]
)
async def test_download_checks_path_components(
    tmp_path, monkeypatch, location
):
    root = tmp_path / "uploads"
    root.mkdir()
    sibling = tmp_path / "uploads-other"
    sibling.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    paths = {
        "inside": root / "own.txt",
        "sibling": sibling / "private.txt",
        "traversal": root / ".." / "uploads-other" / "private.txt",
        "outside": outside / "private.txt",
    }
    path = paths[location]
    path.write_text("synthetic private document", encoding="utf-8")
    row = SimpleNamespace(
        storage_provider="local",
        storage_path=str(path),
        content_type="text/plain",
        original_filename="document.txt",
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: row,
            )
        )
    )
    monkeypatch.setattr(
        files,
        "get_settings",
        lambda: SimpleNamespace(
            upload_dir=str(root),
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=file_app(db)),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/v1/files/{uuid4()}/download",
            params={"project_id": str(uuid4())},
        )
    if location == "inside":
        assert response.status_code == 200
        assert response.text == "synthetic private document"
    else:
        assert response.status_code == 403, response.text
        assert "synthetic private document" not in response.text
    assert path.exists()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in PostgreSQL isolation test",
)
async def test_postgres_file_routes_keep_two_companies_isolated(
    tmp_path, monkeypatch
):
    """Execute real SQL in a private schema and roll back DDL and all data."""
    engine = create_async_engine(os.environ["SAAS_TEST_DATABASE_URL"])
    schema = "rag_boundary_" + uuid4().hex
    monkeypatch.setattr(
        files,
        "get_settings",
        lambda: SimpleNamespace(
            upload_dir=str(tmp_path),
        ),
    )
    companies = [uuid4(), uuid4()]
    search_factory = Mock(
        side_effect=AssertionError("Foreign search executed")
    )
    monkeypatch.setattr(collections, "get_search_service", search_factory)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}"')
                )
                await connection.run_sync(Collection.__table__.create)
                await connection.run_sync(File.__table__.create)
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    rows = []
                    for index, company in enumerate(companies):
                        collection = Collection(
                            project_id=company, display_name=f"企业{index}"
                        )
                        db.add(collection)
                        await db.flush()
                        path = tmp_path / f"company-{index}.txt"
                        path.write_text(
                            f"company-{index}-private", encoding="utf-8"
                        )
                        row = File(
                            project_id=company,
                            collection_id=collection.id,
                            original_filename=path.name,
                            file_size=path.stat().st_size,
                            content_type="text/plain",
                            storage_provider="local",
                            storage_path=str(path),
                        )
                        db.add(row)
                        rows.append(row)
                    await db.commit()
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=file_app(db)),
                        base_url="http://test",
                    ) as client:
                        for index, company in enumerate(companies):
                            own, other = rows[index], rows[1 - index]
                            await assert_collection_isolation(
                                client,
                                company,
                                own.collection_id,
                                other.collection_id,
                            )
                            params = {"project_id": str(company)}
                            listing = await client.get(
                                "/v1/files", params=params
                            )
                            assert listing.status_code == 200, listing.text
                            assert [
                                item["id"] for item in listing.json()["data"]
                            ] == [str(own.id)]
                            assert listing.json()["pagination"]["total"] == 1
                            detail = await client.get(
                                f"/v1/files/{own.id}", params=params
                            )
                            assert detail.status_code == 200, detail.text
                            download = await client.get(
                                f"/v1/files/{own.id}/download", params=params
                            )
                            assert download.status_code == 200
                            assert download.text == f"company-{index}-private"
                            for method, suffix in [
                                ("GET", ""),
                                ("GET", "/download"),
                                ("GET", "/documents"),
                                ("DELETE", ""),
                            ]:
                                denied = await client.request(
                                    method,
                                    f"/v1/files/{other.id}{suffix}",
                                    params=params,
                                )
                                assert denied.status_code == 404, denied.text
                            denied_upload = await client.post(
                                "/v1/files",
                                data={
                                    "project_id": str(company),
                                    "collection_id": str(other.collection_id),
                                },
                                files={
                                    "file": (
                                        "new.txt",
                                        b"synthetic",
                                        "text/plain",
                                    )
                                },
                            )
                            assert (
                                denied_upload.status_code == 404
                            ), denied_upload.text
                            assert (
                                Path(other.storage_path).read_text()
                                == f"company-{1-index}-private"
                            )
                        assert (
                            await db.scalar(
                                select(func.count()).select_from(File)
                            )
                            == 2
                        )
                        search_factory.assert_not_called()
                        # Soft deletion revokes access for the owner too.
                        rows[0].deleted_at = datetime.now(timezone.utc)
                        await db.commit()
                        for suffix in ["", "/download", "/documents"]:
                            denied = await client.get(
                                f"/v1/files/{rows[0].id}{suffix}",
                                params={"project_id": str(companies[0])},
                            )
                            assert denied.status_code == 404, denied.text
                        assert sorted(
                            path.name for path in tmp_path.iterdir()
                        ) == [
                            "company-0.txt",
                            "company-1.txt",
                        ]
            finally:
                await transaction.rollback()
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.schemata "
                        "WHERE schema_name=:name"
                    ),
                    {"name": schema},
                )
                == 0
            )
    finally:
        await engine.dispose()
