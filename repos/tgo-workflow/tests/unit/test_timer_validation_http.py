"""Exercise schedule validation through the real ASGI route contract."""

from collections.abc import AsyncIterator

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio

from app.api.workflows import router


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    application = FastAPI()
    application.include_router(router, prefix="/v1/workflows")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as session:
        yield session


def graph(expression: str, zone: str) -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "timer",
                "type": "timer",
                "position": {"x": 0, "y": 0},
                "data": {
                    "type": "timer",
                    "label": "定时整理",
                    "reference_key": "timer",
                    "cron_expression": expression,
                    "timezone": zone,
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "position": {"x": 0, "y": 100},
                "data": {
                    "type": "answer",
                    "label": "结果",
                    "reference_key": "answer",
                    "output_type": "template",
                    "output_template": "时间预览",
                },
            },
        ],
        "edges": [{"id": "link", "source": "timer", "target": "answer"}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "zone"),
    [
        ("60 * * * *", "UTC"),
        ("0 0 30 2 *", "UTC"),
        ("0 9 * * *", "Unknown/PrivateTenant"),
    ],
)
async def test_invalid_schedule_is_rejected_by_http_validation(
    client: httpx.AsyncClient,
    expression: str,
    zone: str,
) -> None:
    response = await client.post(
        "/v1/workflows/validate",
        params={"project_id": "synthetic-test"},
        json=graph(expression, zone),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_valid_rule_stays_unavailable_until_durable_dispatch_is_ready(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/v1/workflows/validate",
        params={"project_id": "synthetic-test"},
        json=graph("0 9 * * 1-5", "Asia/Shanghai"),
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert any("not supported" in value for value in response.json()["errors"])
    response = await client.get("/v1/workflows/capabilities")
    assert response.status_code == 200
    timer = next(
        item for item in response.json()["nodes"] if item["type"] == "timer"
    )
    assert timer["available"] is False
