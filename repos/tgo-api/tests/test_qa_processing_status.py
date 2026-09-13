"""The authenticated QA proxy preserves worker failure details."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints.rag_qa_pairs import list_qa_pairs
from app.services.rag_client import rag_client
from app.api.v1.endpoints import rag_qa_pairs
from app.schemas.rag import QAPairCreateRequest, QAPairUpdateRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["No active embedding configuration", None])
async def test_list_keeps_processing_failure_reason(monkeypatch, reason):
    project_id, collection_id = uuid4(), uuid4()
    pair = {
        "id": str(uuid4()), "collection_id": str(collection_id),
        "question": "测试问题", "answer": "测试答案", "question_hash": "test",
        "source_type": "manual", "status": "failed" if reason else "processed",
        "error_message": reason, "priority": 0,
        "created_at": "2026-09-08T00:00:00Z",
        "updated_at": "2026-09-08T00:00:00Z",
    }
    request = AsyncMock(return_value={"data": [pair], "total": 1, "limit": 10, "offset": 0})
    monkeypatch.setattr(rag_client, "list_qa_pairs", request)
    result = await list_qa_pairs(
        collection_id, limit=10, offset=0, category=None, status_filter=None,
        current_user=SimpleNamespace(project_id=project_id),
    )
    assert result.model_dump()["data"][0]["error_message"] == reason
    assert request.await_args.kwargs["project_id"] == str(project_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "get"])
@pytest.mark.parametrize("failure", ["HTTP 401，请检查模型配置后重试。", None, "omitted"])
async def test_single_pair_proxy_preserves_failure_and_legacy_responses(
    monkeypatch, action, failure,
):
    project_id, collection_id, pair_id = uuid4(), uuid4(), uuid4()
    pair = {
        "id": str(pair_id), "collection_id": str(collection_id),
        "question": "测试问题", "answer": "测试答案", "question_hash": "test",
        "source_type": "manual", "status": "failed", "priority": 0,
        "created_at": "2026-09-08T00:00:00Z",
        "updated_at": "2026-09-08T00:00:00Z",
    }
    if failure != "omitted":
        pair["error_message"] = failure
    request = AsyncMock(return_value=pair)
    monkeypatch.setattr(rag_client, f"{action}_qa_pair", request)
    user = SimpleNamespace(project_id=project_id)
    if action == "create":
        result = await rag_qa_pairs.create_qa_pair(
            collection_id, QAPairCreateRequest(question="测试问题", answer="测试答案"),
            current_user=user,
        )
    elif action == "update":
        result = await rag_qa_pairs.update_qa_pair(
            pair_id, QAPairUpdateRequest(answer="测试答案"), current_user=user,
        )
    else:
        result = await rag_qa_pairs.get_qa_pair(pair_id, current_user=user)
    assert result.model_dump(mode="json")["error_message"] == (
        None if failure == "omitted" else failure
    )
    assert request.await_args.kwargs["project_id"] == str(project_id)
