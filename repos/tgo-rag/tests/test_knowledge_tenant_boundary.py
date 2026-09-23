"""Exercise QA and website boundaries against disposable PostgreSQL data."""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.rag_service.database import get_db_session_dependency
from src.rag_service.models import (
    Collection,
    CollectionType,
    File,
    FileDocument,
    QAPair,
    WebsitePage,
)
from src.rag_service.routers import qa, websites
from src.rag_service.schemas.qa import compute_question_hash
from src.rag_service.services import qa_documents
from src.rag_service.tasks import qa_processing, website_crawling

pytestmark = pytest.mark.skipif(
    not os.getenv("SAAS_TEST_DATABASE_URL"),
    reason="Opt-in PostgreSQL isolation test",
)


@pytest_asyncio.fixture
async def tenant_knowledge(monkeypatch):
    engine = create_async_engine(os.environ["SAAS_TEST_DATABASE_URL"])
    schema = "knowledge_boundary_" + uuid4().hex
    queued = [Mock() for _ in range(3)]
    for task, replacement in zip(
        [
            qa_processing.process_qa_pair_task,
            qa_processing.process_qa_pairs_batch_task,
            website_crawling.crawl_page_task,
        ],
        queued,
    ):
        monkeypatch.setattr(task, "delay", replacement)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(
                    text(f'SET LOCAL search_path TO "{schema}", public')
                )
                for model in [
                    Collection,
                    File,
                    FileDocument,
                    QAPair,
                    WebsitePage,
                ]:
                    await connection.run_sync(model.__table__.create)
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    tenants = []
                    for index in range(2):
                        company = uuid4()
                        faq = Collection(
                            project_id=company,
                            display_name=f"FAQ-{index}",
                            collection_type=CollectionType.qa,
                        )
                        site = Collection(
                            project_id=company,
                            display_name=f"Site-{index}",
                            collection_type=CollectionType.website,
                            crawl_config={"max_pages": 10},
                        )
                        db.add_all([faq, site])
                        await db.flush()
                        question = f"Question-{index}"
                        pair = QAPair(
                            project_id=company,
                            collection_id=faq.id,
                            question=question,
                            answer=f"Private answer-{index}",
                            question_hash=compute_question_hash(question),
                            category=f"Category-{index}",
                            status="pending",
                        )
                        url = f"https://example.invalid/company-{index}"
                        page = WebsitePage(
                            project_id=company,
                            collection_id=site.id,
                            url=url,
                            url_hash=websites.compute_url_hash(url),
                            status="pending",
                            content_markdown=f"Private-{index}",
                        )
                        db.add_all([pair, page])
                        tenants.append(
                            SimpleNamespace(
                                company=company,
                                faq=faq,
                                site=site,
                                pair=pair,
                                page=page,
                            )
                        )
                    await db.commit()

                    async def database():
                        yield db

                    app = FastAPI()
                    app.include_router(qa.router, prefix="/v1")
                    app.include_router(websites.router, prefix="/v1/websites")
                    app.dependency_overrides[
                        get_db_session_dependency
                    ] = database
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://test",
                    ) as client:
                        yield SimpleNamespace(
                            db=db,
                            tenants=tenants,
                            client=client,
                            queued=queued,
                        )
            finally:
                await outer.rollback()
            remains = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.schemata "
                    "WHERE schema_name = :schema"
                ),
                {"schema": schema},
            )
            assert remains == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1])
async def test_qa_routes_reject_other_company_without_jobs(
    tenant_knowledge, index
):
    state = tenant_knowledge
    own, other = state.tenants[index], state.tenants[1 - index]
    params = {"project_id": str(own.company)}
    client = state.client
    listing = await client.get(
        f"/v1/collections/{own.faq.id}/qa-pairs",
        params=params,
    )
    assert listing.status_code == 200, listing.text
    assert [row["id"] for row in listing.json()["data"]] == [str(own.pair.id)]
    detail = await client.get(f"/v1/qa-pairs/{own.pair.id}", params=params)
    assert detail.json()["answer"] == own.pair.answer
    categories = await client.get("/v1/qa-categories", params=params)
    assert categories.json()["categories"] == [own.pair.category]

    base = f"/v1/collections/{other.faq.id}/qa-pairs"
    pair_url = f"/v1/qa-pairs/{other.pair.id}"
    body = {"question": "New question", "answer": "New answer"}
    for method, path, payload in [
        ("GET", base, None),
        ("GET", pair_url, None),
        ("PUT", pair_url, {"answer": "forbidden"}),
        ("DELETE", pair_url, None),
        ("POST", base, body),
        ("POST", base + "/batch", {"qa_pairs": [body]}),
        (
            "POST",
            base + "/import",
            {"format": "json", "data": json.dumps([body])},
        ),
    ]:
        response = await client.request(
            method, path, params=params, json=payload
        )
        assert response.status_code == 404, (path, response.text)
    filtered = await client.get(
        "/v1/qa-categories",
        params={**params, "collection_id": str(other.faq.id)},
    )
    assert filtered.json()["categories"] == []
    for queued in state.queued:
        queued.assert_not_called()
    await state.db.refresh(other.pair)
    assert other.pair.answer == f"Private answer-{1 - index}"
    assert other.pair.status == "pending" and other.pair.deleted_at is None
    assert await state.db.scalar(select(func.count()).select_from(QAPair)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1])
async def test_website_routes_reject_other_company_without_jobs(
    tenant_knowledge, index
):
    state = tenant_knowledge
    own, other = state.tenants[index], state.tenants[1 - index]
    params = {"project_id": str(own.company)}
    client = state.client
    listing = await client.get(
        "/v1/websites/pages",
        params={
            **params,
            "collection_id": str(own.site.id),
        },
    )
    assert listing.status_code == 200, listing.text
    assert [row["id"] for row in listing.json()["data"]] == [str(own.page.id)]
    detail = await client.get(
        f"/v1/websites/pages/{own.page.id}", params=params
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["url"] == own.page.url
    for suffix in ["pages", "progress"]:
        response = await client.get(
            f"/v1/websites/{suffix}",
            params={
                **params,
                "collection_id": str(other.site.id),
            },
        )
        assert response.status_code == 404, response.text
    base = f"/v1/websites/pages/{other.page.id}"
    for method, suffix, payload in [
        ("GET", "", None),
        ("DELETE", "", None),
        ("POST", "/recrawl", None),
        ("POST", "/crawl-deeper", {"max_depth": 2}),
    ]:
        response = await client.request(
            method, base + suffix, params=params, json=payload
        )
        assert response.status_code == 404, response.text
    response = await client.post(
        "/v1/websites/pages",
        params={
            **params,
            "collection_id": str(other.site.id),
        },
        json={"url": "https://example.invalid/new"},
    )
    assert response.status_code == 404, response.text
    # An own collection cannot use another company's page as the crawl parent.
    parent = await client.post(
        "/v1/websites/pages",
        params={
            **params,
            "collection_id": str(own.site.id),
        },
        json={
            "url": "https://example.invalid/new",
            "parent_page_id": str(other.page.id),
        },
    )
    assert parent.status_code == 400, parent.text
    for queued in state.queued:
        queued.assert_not_called()
    await state.db.refresh(other.page)
    assert other.page.status == "pending"
    assert other.page.content_markdown == f"Private-{1 - index}"
    assert (
        await state.db.scalar(select(func.count()).select_from(WebsitePage))
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1])
async def test_qa_worker_rejects_mismatched_project_before_model(
    tenant_knowledge, monkeypatch, index
):
    state = tenant_knowledge
    own, other = state.tenants[index], state.tenants[1 - index]

    @asynccontextmanager
    async def session():
        yield state.db

    monkeypatch.setattr(qa_documents, "get_db_session", session)
    embedding = AsyncMock(side_effect=AssertionError("Unexpected model call"))
    monkeypatch.setattr(
        qa_documents, "get_embedding_service_for_project", embedding
    )
    result = await qa_documents.process_qa_pair_async(
        other.pair.id, own.company
    )
    assert result["success"] is False
    assert result["error"] == "QA pair not found for project"
    embedding.assert_not_awaited()
    await state.db.refresh(other.pair)
    assert other.pair.status == "pending"
    assert other.pair.document_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize(
    "case", ["valid", "foreign_collection", "deleted_collection"]
)
async def test_crawler_loads_only_matching_active_collection(
    tenant_knowledge, monkeypatch, index, case
):
    state = tenant_knowledge
    own, other = state.tenants[index], state.tenants[1 - index]
    if case == "foreign_collection":
        own.page.collection_id = other.site.id
    elif case == "deleted_collection":
        own.site.deleted_at = datetime.now(timezone.utc)
    await state.db.commit()

    @asynccontextmanager
    async def session():
        yield state.db

    monkeypatch.setattr(website_crawling, "get_db_session", session)
    info, config, error = await website_crawling._load_page_and_config(
        own.page.id
    )
    await state.db.refresh(own.page)
    if case == "valid":
        assert error is None
        assert info.project_id == own.company
        assert info.collection_id == own.site.id
        assert config.max_pages == 10
        assert own.page.status == "crawling"
    else:
        assert error == "Page not found"
        assert info is None and config is None
        assert own.page.status == "pending"
    await state.db.refresh(other.page)
    assert other.page.status == "pending"
