"""Request diagnostics must not copy caller-controlled secrets into logs."""

import logging
import uuid

import httpx
import pytest
from fastapi import FastAPI, Request

from app.main import log_requests


@pytest.mark.asyncio
async def test_request_log_omits_query_values_and_preserves_safe_request_id(
    caplog,
):
    app = FastAPI()
    app.middleware("http")(log_requests)

    @app.get("/health")
    async def health(request: Request):
        return {"request_id": request.state.request_id}

    with caplog.at_level(logging.INFO, logger="app.requests"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/health?access_token=private-sentinel",
                headers={"X-Request-ID": "safe-request-42"},
            )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "safe-request-42"
    assert "private-sentinel" not in caplog.text
    assert "access_token" not in caplog.text
    assert "path=/health" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", ["bad id with spaces", "x" * 1024])
async def test_request_log_replaces_unsafe_request_id(request_id, caplog):
    app = FastAPI()
    app.middleware("http")(log_requests)

    @app.get("/health")
    async def health(request: Request):
        return {"request_id": request.state.request_id}

    with caplog.at_level(logging.INFO, logger="app.requests"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/health", headers={"X-Request-ID": request_id}
            )

    assigned = response.headers["X-Request-ID"]
    assert str(uuid.UUID(assigned)) == assigned
    assert response.json()["request_id"] == assigned
    assert request_id not in caplog.text
