"""The collection page endpoint must serialize current ORM rows, including failures."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.rag_service.database import get_db_session_dependency
from src.rag_service.models import CollectionType, WebsitePage
from src.rag_service.routers.collections import router


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['processed', 'failed'])
async def test_collection_pages_serializes_current_page_model(status):
    project_id, collection_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    page = WebsitePage(
        id=uuid4(), project_id=project_id, collection_id=collection_id,
        parent_page_id=uuid4(), url='https://example.invalid/product',
        url_hash='a' * 64, title='商品资料', depth=1, content_length=20,
        crawl_source='manual', status=status, http_status_code=200,
        error_message='抓取超时，请重试。' if status == 'failed' else None,
        created_at=now, updated_at=now,
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
            collection_type=CollectionType.website,
        )),
        SimpleNamespace(scalar=lambda: 1),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [page])),
    ]))

    async def database():
        yield db

    app = FastAPI()
    app.include_router(router, prefix='/v1/collections')
    app.dependency_overrides[get_db_session_dependency] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url='http://test',
    ) as client:
        response = await client.get(
            f'/v1/collections/{collection_id}/pages',
            params={'project_id': str(project_id), 'status': status},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    row = body['data'][0]
    assert row['id'] == str(page.id)
    assert row['parent_page_id'] == str(page.parent_page_id)
    assert row['crawl_source'] == 'manual'
    assert row['status'] == status
    assert row['error_message'] == page.error_message
    assert 'crawl_job_id' not in row
    assert body['pagination']['total'] == 1
    assert body['pagination']['has_next'] is False
    # Both count and page selection retain tenant, collection and status filters.
    for call in db.execute.await_args_list[1:]:
        values = call.args[0].compile().params.values()
        assert project_id in values and collection_id in values and status in values
