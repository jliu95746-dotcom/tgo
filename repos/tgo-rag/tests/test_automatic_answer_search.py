"""Contract tests for governed automatic-answer retrieval."""

from datetime import UTC, datetime
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.rag_service.routers.collections import router
from src.rag_service.schemas.collections import AutomaticAnswerSearchRequest
from src.rag_service.schemas.knowledge_governance import KnowledgeChannel
from src.rag_service.schemas.search import SearchMetadata, SearchResponse, SearchResult
from src.rag_service.services.search import SearchService
from src.rag_service.services import search as search_module
from sqlalchemy.dialects import postgresql


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
    assert request.min_score == 0.37


def test_router_exposes_separate_automatic_answer_endpoint() -> None:
    paths = {route.path for route in router.routes}

    assert "/{collection_id}/documents/search" in paths
    assert "/{collection_id}/documents/search/automatic-answer" in paths


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawn", [False, True])
async def test_automatic_answer_search_filters_candidates_before_pagination(
    monkeypatch: pytest.MonkeyPatch,
    withdrawn: bool,
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
    semantic_gate = SearchResponse(
        results=[_result(second, 0.82), _result(third, 0.78)],
        search_metadata=SearchMetadata(
            query="退款政策",
            total_results=2,
            returned_results=2,
            search_time_ms=4,
            search_type="semantic",
        ),
    )
    semantic_search = AsyncMock(return_value=semantic_gate)
    hybrid_search = AsyncMock(return_value=base_response)
    eligible_document_ids = AsyncMock(return_value={second, third})
    monkeypatch.setattr(service, "semantic_search", semantic_search)
    monkeypatch.setattr(service, "hybrid_search", hybrid_search)
    monkeypatch.setattr(service, "_eligible_document_ids", eligible_document_ids)
    full_content = "知识库简介。" * 60 + "云朵法棍包：牛皮，莓果红。"
    contents = AsyncMock(return_value=(
        {third: "售后规则"} if withdrawn
        else {second: full_content, third: "售后规则"}
    ))
    monkeypatch.setattr(service, "_eligible_document_contents", contents, raising=False)

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
    semantic_search.assert_awaited_once_with(
        query="退款政策",
        project_id=project_id,
        collection_id=collection_id,
        limit=3,
        min_score=0.2,
        filters={"language": "zh"},
    )
    eligible_document_ids.assert_awaited_once()
    assert [result.document_id for result in response.results] == [
        third if withdrawn else second
    ]
    assert response.results[0].content == ("售后规则" if withdrawn else full_content)
    if not withdrawn:
        assert "莓果红" in response.model_dump(mode="json")["results"][0]["content"]
    contents.assert_awaited_once_with(
        project_id=project_id,
        collection_id=collection_id,
        channel=KnowledgeChannel.WECOM_KF,
        candidate_ids=(second, third),
    )
    assert response.search_metadata.total_results == (1 if withdrawn else 2)
    assert response.search_metadata.returned_results == 1
    assert response.search_metadata.search_type == "hybrid_rrf_governed"
    assert response.search_metadata.filters_applied == {
        "language": "zh",
        "automatic_answer": True,
        "knowledge_channel": "wecom_kf",
    }


@pytest.mark.asyncio
async def test_automatic_answer_search_rejects_hybrid_hits_without_absolute_semantic_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    collection_id = uuid4()
    candidate = uuid4()
    empty_semantic_gate = SearchResponse(
        results=[],
        search_metadata=SearchMetadata(
            query="汽车机油多久更换",
            total_results=0,
            returned_results=0,
            search_time_ms=5,
            search_type="semantic",
        ),
    )
    normalized_hybrid_hit = SearchResponse(
        results=[_result(candidate, 1.0)],
        search_metadata=SearchMetadata(
            query="汽车机油多久更换",
            total_results=1,
            returned_results=1,
            search_time_ms=8,
            search_type="hybrid_rrf",
        ),
    )

    service = SearchService.__new__(SearchService)
    service.settings = SimpleNamespace(candidate_multiplier=2)
    monkeypatch.setattr(
        service,
        "semantic_search",
        AsyncMock(return_value=empty_semantic_gate),
    )
    monkeypatch.setattr(
        service,
        "hybrid_search",
        AsyncMock(return_value=normalized_hybrid_hit),
    )
    eligible_document_ids = AsyncMock(return_value={candidate})
    monkeypatch.setattr(service, "_eligible_document_ids", eligible_document_ids)
    monkeypatch.setattr(
        service, "_eligible_document_contents", AsyncMock(return_value={}), raising=False
    )

    response = await service.automatic_answer_search(
        query="汽车机油多久更换",
        project_id=project_id,
        collection_id=collection_id,
        channel=KnowledgeChannel.WEB,
        limit=10,
        min_score=0.37,
        search_mode="hybrid",
    )

    assert response.results == []
    eligible_document_ids.assert_awaited_once_with(
        project_id=project_id,
        channel=KnowledgeChannel.WEB,
        candidate_ids=(),
    )


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
    monkeypatch.setattr(
        service, "_eligible_document_contents", AsyncMock(return_value={}), raising=False
    )

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


@pytest.mark.asyncio
async def test_full_content_read_rechecks_governance_and_collection(monkeypatch):
    project_id, collection_id, document_id = uuid4(), uuid4(), uuid4()
    db = SimpleNamespace(execute=AsyncMock(
        return_value=Mock(all=Mock(return_value=[(document_id, "完整正文")]))
    ))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(search_module, "get_db_session", session)
    service = SearchService.__new__(SearchService)
    contents = await service._eligible_document_contents(
        project_id=project_id, collection_id=collection_id,
        channel=KnowledgeChannel.WEB, candidate_ids=(document_id,),
    )
    assert contents == {document_id: "完整正文"}
    sql = str(db.execute.await_args.args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    for gate in (
        "review_status = 'approved'", "allow_automatic_reply IS true",
        "effective_at <=", "expires_at IS NULL", "source_origin != 'customer'",
        "deleted_at IS NULL", "channels @> ARRAY['web']",
        str(project_id), str(collection_id), str(document_id),
    ):
        assert gate in sql


@pytest.mark.asyncio
async def test_empty_content_candidates_do_not_query_database(monkeypatch):
    session = Mock(side_effect=AssertionError("must not query database"))
    monkeypatch.setattr(search_module, "get_db_session", session)
    service = SearchService.__new__(SearchService)
    assert await service._eligible_document_contents(
        project_id=uuid4(), collection_id=uuid4(),
        channel=KnowledgeChannel.WEB, candidate_ids=(),
    ) == {}


def test_router_preserves_full_content_only_for_automatic_answers():
    models = {route.path: route.response_model for route in router.routes}
    automatic = models["/{collection_id}/documents/search/automatic-answer"]
    schema = automatic.model_json_schema()
    assert any(
        "content" in item.get("required", []) for item in schema["$defs"].values()
    )
    assert "content" not in _result(uuid4(), 0.9).model_dump()
