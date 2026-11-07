"""Validation returns declared fields and never echoes request contents."""

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError

from app.main import app as runtime_app
from app.api.connection_tests import PrivateTestRoute
from app.schemas.install import PluginInstallRequest

PRIVATE = "synthetic-private-validation-349"


def probe_app():
    app = FastAPI()
    app.exception_handlers.update(runtime_app.exception_handlers)

    @app.post("/install")
    async def install(request: PluginInstallRequest):
        return {"id": request.id}

    @app.get("/query")
    async def query(count: int):
        return {"count": count}

    @app.post("/forged")
    async def forged(request: PluginInstallRequest):
        raise RequestValidationError(
            [
                {
                    "type": PRIVATE,
                    "loc": ("body", "runtime", PRIVATE),
                    "msg": PRIVATE,
                    "input": PRIVATE,
                    "ctx": {"secret": PRIVATE},
                }
            ]
        )

    private_routes = APIRouter(route_class=PrivateTestRoute)

    @private_routes.post("/private")
    async def private(request: dict[str, int]):
        return request

    app.include_router(private_routes)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "nested", "query", "forged"])
async def test_validation_payload_and_dynamic_locations_stay_private(kind):
    app = probe_app()
    payload = {
        "id": "fixture",
        "project_id": "project",
        "name": "Fixture",
        "version": "1",
        "source": {"binary": {"url": "https://source.test"}},
        "runtime": {"env": {"TOKEN": PRIVATE}},
    }
    path = "/install"
    if kind == "missing":
        del payload["name"]
    elif kind == "nested":
        payload["runtime"]["env"] = {PRIVATE: {"private": PRIVATE}}
    elif kind == "forged":
        path = "/forged"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = (
            await client.get("/query", params={"count": PRIVATE})
            if kind == "query"
            else await client.post(path, json=payload)
        )
    assert response.status_code == 422
    assert PRIVATE not in response.text
    for issue in response.json()["detail"]:
        assert set(issue) == {"loc", "type", "msg"}
        assert issue["loc"][0] in {"body", "query"}
    if kind == "missing":
        assert response.json()["detail"][0]["loc"] == ["body", "name"]


@pytest.mark.asyncio
async def test_private_validation_matches_declared_schema():
    app = probe_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/private", json={PRIVATE: "invalid"})
    assert response.status_code == 422 and PRIVATE not in response.text
    required = app.openapi()["components"]["schemas"]["ValidationError"][
        "required"
    ]
    assert set(response.json()["detail"][0]) == set(required)
