"""Search pagination must count authorized messages, not stale IM membership."""

from unittest.mock import AsyncMock
import asyncio

import httpx
import pytest

from app.api.v1.endpoints import search
from app.core.config import settings
from app.schemas.wukongim import (
    WuKongIMSearchMessagesResponse, WuKongIMSearchResult,
)
from tests.test_conversation_tenant_boundary import channel_app


def message(number, channel_id, channel_type=251):
    return WuKongIMSearchResult(
        message_id=number, message_seq=number, from_uid="sender",
        channel_id=channel_id, channel_type=channel_type, timestamp=number,
        payload={"content": f"message-{number}"},
    )


@pytest.fixture
def search_app(channel_app, monkeypatch):
    app, db, companies, staff, visitors, _ = channel_app
    app.include_router(search.router, prefix="/search")
    monkeypatch.setattr(search.wukongim_client, "enabled", True)
    return app, db, companies, staff, visitors


def install_results(monkeypatch, records):
    async def source(**kwargs):
        start = (kwargs["page"] - 1) * kwargs["limit"]
        return WuKongIMSearchMessagesResponse(
            total=len(records), messages=records[start:start + kwargs["limit"]],
        )
    mock = AsyncMock(side_effect=source)
    monkeypatch.setattr(search.wukongim_client, "search_user_messages", mock)
    return mock


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize("page,expected,next_page", [
    (1, [101, 102], True), (2, [103, 104], True), (3, [105], False),
])
async def test_filtered_search_has_no_foreign_messages_or_totals(
    search_app, monkeypatch, role, page, expected, next_page,
):
    app, _, companies, staff, visitors = search_app
    staff[0].role = role
    foreign = [message(index, f"{companies[1].id}-prj", 249)
               for index in range(1, 101)]
    own = [message(index, f"{visitors[0].id}-vtr") for index in range(101, 106)]
    source = install_results(monkeypatch, foreign + own)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.get("/search", params={
            "q": "message", "scope": "messages", "message_page": page,
            "message_page_size": 2, "project_id": str(companies[1].id),
        })
    assert result.status_code == 200
    body = result.json()
    assert [row["message_id"] for row in body["messages"]] == expected
    assert body["message_count"] == len(expected)
    assert body["message_pagination"] == {
        "page": page, "page_size": 2, "total": 5,
        "has_next": next_page, "has_previous": page > 1,
    }
    assert all(call.kwargs["uid"] == f"{staff[0].id}-staff"
               for call in source.await_args_list)


@pytest.mark.asyncio
async def test_search_does_not_guess_a_total_before_exhaustion(search_app, monkeypatch):
    app, _, _, _, visitors = search_app
    records = [message(index, f"{visitors[0].id}-vtr") for index in range(1, 302)]
    source = install_results(monkeypatch, records)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.get("/search", params={
            "q": "message", "scope": "messages", "message_page_size": 2,
        })
    assert result.status_code == 200
    assert [row["message_id"] for row in result.json()["messages"]] == [1, 2]
    assert result.json()["message_pagination"]["total"] is None
    assert result.json()["message_pagination"]["has_next"] is True
    assert source.await_count == 1


@pytest.mark.asyncio
async def test_scan_limit_returns_an_error_instead_of_partial_results(search_app, monkeypatch):
    app, _, companies, _, _ = search_app
    monkeypatch.setattr(settings, "MESSAGE_SEARCH_SCAN_LIMIT", 100)
    source = install_results(monkeypatch, [
        message(index, f"{companies[1].id}-prj", 249) for index in range(1, 202)
    ])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.get("/search", params={"q": "message", "scope": "messages"})
    assert result.status_code == 422
    assert "MESSAGE_SEARCH_LIMIT_EXCEEDED" in result.text
    assert "message-" not in result.text
    assert source.await_count == 1


@pytest.mark.asyncio
async def test_slow_source_is_cancelled_without_a_partial_response(search_app, monkeypatch):
    app, _, _, _, _ = search_app
    monkeypatch.setattr(settings, "MESSAGE_SEARCH_TIMEOUT_SECONDS", 0.1)
    cancelled = asyncio.Event()

    async def slow_source(**kwargs):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()

    monkeypatch.setattr(search.wukongim_client, "search_user_messages", slow_source)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.get("/search", params={"q": "message", "scope": "messages"})
    assert result.status_code == 504
    assert "MESSAGE_SEARCH_TIMEOUT" in result.text
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_duplicate_hits_are_not_counted_twice(search_app, monkeypatch):
    app, _, _, _, visitors = search_app
    record = message(1, f"{visitors[0].id}-vtr")
    install_results(monkeypatch, [record, record])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        result = await client.get("/search", params={"q": "message", "scope": "messages"})
    assert result.status_code == 200
    assert result.json()["message_pagination"]["total"] == 1
    assert result.json()["message_count"] == 1
