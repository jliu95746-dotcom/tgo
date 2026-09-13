"""The authenticated API must not silently discard crawler settings."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.rag_websites import add_page
from app.schemas.rag import (
    AddPageRequest, CollectionCreateRequest, CollectionUpdateRequest,
    CrawlConfig, CrawlOptionsSchema, FileResponse,
)
from app.services.rag_client import rag_client


@pytest.mark.asyncio
async def test_public_proxy_preserves_explicit_options_only(monkeypatch):
    project_id, collection_id = uuid4(), uuid4()
    request = AddPageRequest(url="https://example.test/a", options={
        "delay_seconds": 0.25, "timeout_seconds": 12,
        "wait_time": 2, "headers": {"X-Fixture": "owned"},
    })
    proxy = AsyncMock(return_value={
        "success": True, "page_id": str(uuid4()),
        "message": "queued", "status": "added",
    })
    monkeypatch.setattr(rag_client, "add_website_page", proxy)
    await add_page(
        request, collection_id, SimpleNamespace(project_id=project_id),
    )
    options = proxy.await_args.kwargs["page_data"]["options"]
    assert options == {
        "delay_seconds": 0.25, "timeout_seconds": 12,
        "wait_time": 2, "headers": {"X-Fixture": "owned"},
    }


@pytest.mark.parametrize("options", [
    {"delay_seconds": -1}, {"delay_seconds": 61},
    {"timeout_seconds": 4}, {"timeout_seconds": 301}, {"wait_time": 31},
])
def test_invalid_options_are_rejected_before_proxy(options):
    with pytest.raises(ValidationError):
        CrawlOptionsSchema.model_validate(options)


@pytest.mark.parametrize(
    "schema", [CollectionCreateRequest, CollectionUpdateRequest],
)
def test_collection_proxy_preserves_canonical_crawler_settings(schema):
    config = {
        "render_js": False, "wait_time": 2.5, "delay_seconds": 0,
        "timeout_seconds": 12, "follow_external_links": True,
        "headers": {"X-Fixture": "owned"}, "user_agent": "Owned/1",
    }
    request = schema.model_validate({
        "display_name": "owned", "collection_type": "website",
        "crawl_config": config,
    })
    forwarded = request.model_dump(exclude_none=True)["crawl_config"]
    for name, value in config.items():
        assert forwarded.get(name) == value
    # GET/list response validation uses the same config schema.
    returned = CrawlConfig.model_validate(forwarded).model_dump(
        exclude_none=True,
    )
    assert returned == forwarded


def test_collection_legacy_settings_are_not_masked_by_new_defaults():
    saved = CrawlConfig.model_validate({
        "js_rendering": False, "timeout": 1, "delay_between_requests": 60,
    }).model_dump(exclude_none=True)
    assert saved["js_rendering"] is False and saved["timeout"] == 1
    assert saved["delay_between_requests"] == 60
    for name in ("render_js", "timeout_seconds", "delay_seconds", "wait_time"):
        assert name not in saved


def test_file_failure_reason_survives_public_proxy_response():
    payload = {
        "id": str(uuid4()), "original_filename": "owned.md", "file_size": 1,
        "content_type": "text/markdown", "status": "failed",
        "document_count": 0, "total_tokens": 0,
        "created_at": "2026-09-08T00:00:00Z",
        "updated_at": "2026-09-08T00:00:00Z",
        "error_message": "知识库处理任务未能提交，请稍后重新抓取。",
    }
    returned = FileResponse.model_validate(payload).model_dump()
    assert returned.get("error_message") == payload["error_message"]
