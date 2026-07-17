"""Contract tests for governed automatic-answer retrieval."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.rag_service.routers.collections import router
from src.rag_service.schemas.collections import AutomaticAnswerSearchRequest
from src.rag_service.schemas.knowledge_governance import KnowledgeChannel
from src.rag_service.schemas.search import SearchMetadata, SearchResponse, SearchResult
from src.rag_service.services.search import SearchService


def _result(document_id: UUID, score: float) -> SearchResult:
    return SearchResult(
        document_id=document_id,
        file_id=uuid4(),
        collection_id=uuid4(),
        relevance_score=score,
        content_preview=f"content-{document_id}",
        content_type="paragraph",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def test_automatic_answer_request_requires_supported_channel() -> None:
    with pytest.raises(ValidationError):
        AutomaticAnswerSearchRequest(query="退款政策")

    with pytest.raises(ValidationError):
        AutomaticAnswerSearchRequest(query="退款政策", channel="unsupported")

    request = AutomaticAnswerSearchRequest(
        query="退款政策",
        channel=KnowledgeChannel.WECOM_KF,
    )
    assert request.channel is KnowledgeChannel.WECOM_KF


def test_router_exposes_separate_automatic_answer_endpoint() -> None:
    paths = {route.path for route in router.routes}

    assert "/{collection_id}/documents/search" in paths
    assert "/{collection_id}/documents/search/automatic-answer" in paths


@pytest.mark.asyncio
async def test_automatic_answer_search_filters_candidates_before_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    collection_id = uuid4()
    first, second, third = uuid4(), uuid4(), uuid4()
    base_response = SearchResponse(
        results=[_result(first, 0.95), _result(second, 0.9), _result(third, 0.85)],
        search_metadata=SearchMetadata(
            query="退款政策",
            total_results=3,
            returned_results=3,
            search_time_ms=8,
            filters_applied={"language": "zh"},
            search_type="hybrid_rrf",
        ),
    )

    service = SearchService.__new__(SearchService)
    service.settings = SimpleNamespace(candidate_multiplier=3)
    hybrid_search = AsyncMock(return_value=base_response)
    eligible_document_ids = AsyncMock(return_value={second, third})
    monkeypatch.setattr(service, "hybrid_search", hybrid_search)
    monkeypatch.setattr(service, "_eligible_document_ids", eligible_document_ids)

    response = await service.automatic_answer_search(
        query="退款政策",
        project_id=project_id,
        collection_id=collection_id,
        channel=KnowledgeChannel.WECOM_KF,
        limit=1,
        offset=0,
        min_score=0.2,
        filters={"language": "zh"},
        search_mode="hybrid",
    )

    hybrid_search.assert_awaited_once_with(
        query="退款政策",
        project_id=project_id,
        collection_id=collection_id,
        limit=3,
        min_score=0.2,
        filters={"language": "zh"},
    )
    eligible_document_ids.assert_awaited_once()
    assert [result.document_id for result in response.results] == [second]
    assert response.search_metadata.total_results == 2
    assert response.search_metadata.returned_results == 1
    assert response.search_metadata.search_type == "hybrid_rrf_governed"
    assert response.search_metadata.filters_applied == {
        "language": "zh",
        "automatic_answer": True,
        "knowledge_channel": "wecom_kf",
    }


@pytest.mark.asyncio
async def test_automatic_answer_search_fails_closed_when_no_candidate_is_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    collection_id = uuid4()
    candidate = uuid4()
    base_response = SearchResponse(
        results=[_result(candidate, 0.95)],
        search_metadata=SearchMetadata(
            query="未审核知识",
            total_results=1,
            returned_results=1,
            search_time_ms=4,
            search_type="semantic",
        ),
    )

    service = SearchService.__new__(SearchService)
    service.settings = SimpleNamespace(candidate_multiplier=2)
    monkeypatch.setattr(service, "semantic_search", AsyncMock(return_value=base_response))
    monkeypatch.setattr(service, "_eligible_document_ids", AsyncMock(return_value=set()))

    response = await service.automatic_answer_search(
        query="未审核知识",
        project_id=project_id,
        collection_id=collection_id,
        channel=KnowledgeChannel.WEB,
        limit=10,
        offset=0,
        search_mode="embedding",
    )

    assert response.results == []
    assert response.search_metadata.total_results == 0
    assert response.search_metadata.returned_results == 0
