"""Child page reservation is deduplicated before quota and inherits overrides."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.rag_service.services import website_discovery as discovery


def test_duplicates_do_not_consume_remaining_quota():
    urls = ["https://example.test/old", "https://example.test/new",
            "https://example.test/new"]
    selected = discovery.select_new_urls(urls, {discovery.url_hash(urls[0])}, 1)
    assert selected == [urls[1]]


@pytest.mark.asyncio
async def test_reservation_inherits_settings_and_replaces_json_value():
    old_url, new_url = "https://example.test/old", "https://example.test/new"
    links = [{"url": old_url, "created": False}, {"url": new_url, "created": False}]
    source = SimpleNamespace(
        id=uuid4(), project_id=uuid4(), collection_id=uuid4(), depth=2,
        crawl_config={"render_js": False, "headers": {"X-Fixture": "owned"},
                      "headers_origin": "https://example.test/root"},
        discovered_links=links,
    )
    collection = SimpleNamespace(crawl_config={"max_pages": 3})
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(one_or_none=lambda: (source, collection)),
        SimpleNamespace(scalars=lambda: SimpleNamespace(
            all=lambda: [discovery.url_hash(old_url)],
        )),
    ]
    db.scalar.return_value = 2
    created = []

    def add(page):
        page.id = uuid4()
        created.append(page)

    db.add = add
    reserved = await discovery.reserve_child_pages(
        db, source.id, source.project_id, [old_url, new_url, new_url], 4,
        overrides={"include_patterns": []},
    )
    assert reserved.urls == [new_url] and len(created) == 1
    assert created[0].depth == 3 and created[0].crawl_config == {
        **source.crawl_config, "max_depth": 4, "include_patterns": [],
    }
    assert source.discovered_links is not links
    assert all(link["created"] for link in source.discovered_links)
    assert not any(link["created"] for link in links)
    assert db.execute.await_args_list[0].args[0]._for_update_arg is not None


@pytest.mark.asyncio
async def test_deleted_page_or_collection_cannot_create_children():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(one_or_none=lambda: None)
    reserved = await discovery.reserve_child_pages(
        db, uuid4(), uuid4(), ["https://example.test/new"], 3,
    )
    assert reserved.urls == [] and reserved.page_ids == []
    db.scalar.assert_not_awaited()
