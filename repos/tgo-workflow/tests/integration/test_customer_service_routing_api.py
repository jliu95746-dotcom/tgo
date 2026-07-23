"""HTTP contract for the customer-service routing policy."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_route_customer_service_message() -> None:
    payload = {
        "classification": {
            "intent": "logistics_query",
            "confidence": 0.95,
            "risk_level": "low",
            "recommended_route": "read_only_tool",
            "need_human": False,
            "taxonomy_version": "v1",
        },
        "media_status": "not_applicable",
        "content_sources": ["user_text"],
        "content_trust_boundary": "untrusted_customer_content",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/v1/customer-service/route", json=payload)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "target": "read_only_tool",
        "reason": "read_only_query",
        "content_trust_boundary": "untrusted_customer_content",
        "execute_tool": False,
    }
