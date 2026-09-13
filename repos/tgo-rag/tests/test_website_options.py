"""Single-page overrides are isolated and use absolute depth at runtime."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.rag_service.routers import websites
from src.rag_service.schemas.websites import AddPageRequest
from src.rag_service.tasks.website_crawling import crawl_page_task


@pytest.mark.asyncio
async def test_add_child_preserves_collection_options_and_relative_depth(monkeypatch):
    project_id, collection_id, parent_id = uuid4(), uuid4(), uuid4()
    config = {"render_js": True, "respect_robots_txt": True, "max_depth": 3}
    collection = SimpleNamespace(crawl_config=config.copy())
    parent = SimpleNamespace(depth=2, crawl_config={"user_agent": "Parent/1"})
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: collection),
        SimpleNamespace(scalar_one_or_none=lambda: parent),
    ]
    db.scalar.return_value = 1
    created = []

    def add(page):
        page.id = uuid4()
        created.append(page)

    db.add = add
    monkeypatch.setattr(websites, "check_url_exists_in_collection",
                        AsyncMock(return_value=(False, None)))
    delay = Mock(return_value=SimpleNamespace(id="owned-task"))
    monkeypatch.setattr(crawl_page_task, "delay", delay)
    request = AddPageRequest(url="https://example.test/child", parent_page_id=parent_id,
                             max_depth=0, include_patterns=[],
                             options={"respect_robots_txt": False, "delay_seconds": 0})
    result = await websites.add_page(request, collection_id, project_id, db)
    assert result.success
    assert collection.crawl_config == config
    assert created[0].crawl_config == {
        "user_agent": "Parent/1", "respect_robots_txt": False,
        "delay_seconds": 0, "include_patterns": [], "max_depth": 3,
    }
    assert delay.call_args.kwargs["max_depth"] == 3
    assert delay.call_args.kwargs["auto_discover"] is False


@pytest.mark.asyncio
async def test_manual_page_respects_collection_quota(monkeypatch):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(crawl_config={"max_pages": 2}),
    )
    db.scalar.return_value = 2
    db.add = Mock()
    monkeypatch.setattr(websites, "check_url_exists_in_collection",
                        AsyncMock(return_value=(False, None)))
    delay = Mock()
    monkeypatch.setattr(crawl_page_task, "delay", delay)
    result = await websites.add_page(
        AddPageRequest(url="https://example.test/full"), uuid4(), uuid4(), db,
    )
    assert not result.success and result.status == "limit_reached"
    db.add.assert_not_called()
    delay.assert_not_called()
    statement = db.execute.await_args.args[0]
    assert statement._for_update_arg is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_external_child_does_not_inherit_source_headers(monkeypatch, external):
    from src.rag_service.tasks import website_crawling as tasks
    from src.rag_service.services.crawl_errors import CrawlError

    source_url = "https://source.test/page"
    target_url = "https://external.test/page" if external else source_url
    page = tasks.PageInfo(uuid4(), target_url, 1, uuid4(), uuid4(), {})
    config = tasks.CrawlConfig.from_dict({
        "headers": {"Authorization": "owned-fixture"}, "headers_origin": source_url,
        "follow_external_links": True, "wait_time": 2,
    })
    monkeypatch.setattr(tasks, "_load_page_and_config",
                        AsyncMock(return_value=(page, config, None)))
    monkeypatch.setattr(tasks, "_update_page_status", AsyncMock())
    crawler = Mock(return_value=SimpleNamespace(
        crawl_page=AsyncMock(side_effect=CrawlError("empty_content")),
    ))
    monkeypatch.setattr(tasks, "WebCrawlerService", crawler)
    await tasks.crawl_page_async(page.id)
    options = crawler.call_args.kwargs["options"]
    assert options.headers == (None if external else {"Authorization": "owned-fixture"})
    assert options.follow_external_links and options.wait_time == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_external_links_are_followed_only_when_enabled(enabled):
    from src.rag_service.services.crawler import CrawlOptions, WebCrawlerService

    service = WebCrawlerService(CrawlOptions(follow_external_links=enabled))
    result = SimpleNamespace(links={
        "internal": [{"href": "/same"}],
        "external": [{"href": "https://external.test/other"}],
    })
    links = service._extract_links(result, "https://source.test/root")
    assert ("https://external.test/other" in links) is enabled
    assert service.filter_links(links, "https://source.test/root") == links


@pytest.mark.asyncio
async def test_add_page_reports_dispatch_failure_and_records_retryable_state(
    monkeypatch,
):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(crawl_config={}),
    )
    db.scalar.return_value = 0
    db.add = lambda page: setattr(page, "id", uuid4())
    monkeypatch.setattr(websites, "check_url_exists_in_collection",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(crawl_page_task, "delay",
                        Mock(side_effect=RuntimeError("private-broker")))
    with pytest.raises(HTTPException) as error:
        await websites.add_page(AddPageRequest(url="https://example.test/new"),
                                uuid4(), uuid4(), db)
    assert error.value.status_code == 503
    assert "private-broker" not in str(error.value.detail)
    assert "UPDATE rag_website_pages" in str(db.execute.await_args.args[0])
