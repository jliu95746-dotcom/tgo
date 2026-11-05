"""Store proxy methods retain transport and authentication contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
import httpx
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.v1.endpoints import store


METHODS = ("get", "post", "put", "delete", "patch")
PROXY_PATH = "/v1/store/proxy/{path}"


def proxy_app() -> FastAPI:
    application = FastAPI()
    application.include_router(store.router, prefix="/v1/store")
    return application


def test_proxy_operation_ids_are_unique_for_each_supported_method():
    operation = proxy_app().openapi()["paths"][PROXY_PATH]
    identifiers = [operation[method]["operationId"] for method in METHODS]
    assert set(operation) == set(METHODS)
    assert len(set(identifiers)) == len(METHODS)
    for method, identifier in zip(METHODS, identifiers):
        assert identifier.endswith("_" + method)


def test_proxy_operation_ids_do_not_depend_on_python_hash_seed():
    program = "\n".join(
        [
            "import json",
            "from fastapi import FastAPI",
            "from app.api.v1.endpoints.store import router",
            "app = FastAPI()",
            "app.include_router(router, prefix='/v1/store')",
            "operations = app.openapi()['paths']['/v1/store/proxy/{path}']",
            "print(json.dumps({key: value['operationId'] "
            "for key, value in operations.items()}, sort_keys=True))",
        ]
    )
    results = []
    for seed in ("0", "1", "2"):
        run = subprocess.run(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        results.append(json.loads(run.stdout.strip().splitlines()[-1]))
    assert results[0] == results[1] == results[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", METHODS)
async def test_all_proxy_methods_forward_opaque_requests(monkeypatch, method):
    application = proxy_app()
    application.dependency_overrides[store.get_current_active_user] = (
        lambda: SimpleNamespace()
    )
    upstream = AsyncMock()
    upstream.request.return_value = httpx.Response(
        207, content=b"synthetic-upstream", headers={"x-synthetic": "yes"}
    )
    context = AsyncMock()
    context.__aenter__.return_value = upstream
    monkeypatch.setattr(store.httpx, "AsyncClient", lambda **kwargs: context)
    monkeypatch.setattr(
        store.settings, "STORE_SERVICE_URL", "https://store.example.invalid"
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.request(
            method.upper(),
            "/v1/store/proxy/models/example?tag=a&tag=b",
            content=b"synthetic-opaque-request",
            headers={
                "Authorization": "Bearer synthetic-tgo-credential",
                "X-Store-Authorization": "Bearer synthetic-store-credential",
                "X-Synthetic-Header": "preserved",
            },
        )
    assert response.status_code == 207
    assert response.content == b"synthetic-upstream"
    assert response.headers["x-synthetic"] == "yes"
    arguments = upstream.request.await_args.kwargs
    assert arguments["method"] == method.upper()
    assert arguments["url"] == (
        "https://store.example.invalid/api/v1/models/example?tag=a&tag=b"
    )
    assert arguments["content"] == b"synthetic-opaque-request"
    assert arguments["headers"]["authorization"] == (
        "Bearer synthetic-store-credential"
    )
    assert arguments["headers"]["x-synthetic-header"] == "preserved"
    assert "Bearer synthetic-tgo-credential" not in str(arguments["headers"])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", METHODS)
async def test_all_proxy_methods_keep_the_staff_authentication_dependency(
    monkeypatch, method
):
    application = proxy_app()

    def denied():
        raise HTTPException(status_code=401, detail="Authentication required")

    def unexpected_request(**kwargs):
        pytest.fail("unauthenticated requests must not contact the store")

    application.dependency_overrides[store.get_current_active_user] = denied
    monkeypatch.setattr(store.httpx, "AsyncClient", unexpected_request)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.request(
            method.upper(), "/v1/store/proxy/models"
        )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("method", METHODS)
async def test_retired_features_remain_closed_for_all_proxy_methods(
    monkeypatch, method
):
    application = proxy_app()
    application.dependency_overrides[store.get_current_active_user] = (
        lambda: SimpleNamespace()
    )

    def unexpected_request(**kwargs):
        pytest.fail("retired features must not contact the store")

    monkeypatch.setattr(store.httpx, "AsyncClient", unexpected_request)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.request(
            method.upper(), "/v1/store/proxy/agents/example/install"
        )
    assert response.status_code == 410
