"""Download errors retain status without exposing upstream bodies or errors."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.endpoints import rag_files
from app.core.security import get_authenticated_project

PRIVATE = "never-log-download-private-token"


@pytest.fixture
def download_case(monkeypatch):
    file_id, project_id = uuid4(), uuid4()
    fetch = AsyncMock()
    monkeypatch.setattr(rag_files.rag_client, "download_file", fetch)

    async def download():
        return await rag_files.download_file(
            file_id,
            (SimpleNamespace(id=project_id), "synthetic-key"),
        )

    return SimpleNamespace(
        file_id=file_id,
        project_id=project_id,
        fetch=fetch,
        download=download,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429, 500, 503])
@pytest.mark.parametrize("json_body", [False, True])
async def test_upstream_error_status_survives_without_body_or_private_logs(
    download_case,
    caplog,
    status,
    json_body,
):
    response = (
        httpx.Response(status, json={"error": {"message": PRIVATE}})
        if json_body
        else httpx.Response(status, text=PRIVATE)
    )
    download_case.fetch.return_value = response
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await download_case.download()
    assert rejected.value.status_code == status
    assert PRIVATE not in repr(rejected.value.detail)
    assert PRIVATE not in repr([vars(record) for record in caplog.records])
    download_case.fetch.assert_awaited_once_with(
        project_id=str(download_case.project_id),
        file_id=str(download_case.file_id),
    )


@pytest.mark.asyncio
async def test_binary_content_and_headers_survive_without_header_logs(
    download_case,
    caplog,
):
    body = b"\xff\x00file"
    content_type = f"application/x-test; sample={PRIVATE}"
    disposition = 'attachment; filename="sample.bin"'
    download_case.fetch.return_value = httpx.Response(
        200,
        content=body,
        headers={
            "content-type": content_type,
            "content-disposition": disposition,
            "content-length": str(len(body)),
        },
    )
    with caplog.at_level("INFO"):
        response = await download_case.download()
    assert b"".join([piece async for piece in response.body_iterator]) == body
    assert response.headers["content-type"] == content_type
    assert response.headers["content-disposition"] == disposition
    assert response.headers["content-length"] == str(len(body))
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


class UnprintableError(Exception):
    def __str__(self):
        raise RuntimeError(PRIVATE)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError(PRIVATE), UnprintableError()])
async def test_unexpected_error_is_not_formatted_or_chained_to_public_error(
    download_case,
    caplog,
    error,
):
    download_case.fetch.side_effect = error
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await download_case.download()
    assert rejected.value.status_code == 500
    assert rejected.value.__suppress_context__
    assert PRIVATE not in repr(rejected.value.detail)
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.asyncio
async def test_download_cancellation_propagates_without_public_error(
    download_case,
):
    signal = asyncio.CancelledError("stop-download")
    download_case.fetch.side_effect = signal
    with pytest.raises(asyncio.CancelledError) as stopped:
        await download_case.download()
    assert stopped.value is signal


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 503])
async def test_http_download_response_retains_error_status_and_hides_body(
    download_case,
    caplog,
    status,
):
    download_case.fetch.return_value = httpx.Response(status, text=PRIVATE)
    app = FastAPI()
    app.include_router(rag_files.router, prefix="/files")
    app.dependency_overrides[get_authenticated_project] = lambda: (
        SimpleNamespace(id=download_case.project_id),
        "synthetic-key",
    )
    with caplog.at_level("INFO"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                f"/files/{download_case.file_id}/download"
            )
    assert response.status_code == status
    assert PRIVATE not in response.text
    assert PRIVATE not in repr([vars(record) for record in caplog.records])
