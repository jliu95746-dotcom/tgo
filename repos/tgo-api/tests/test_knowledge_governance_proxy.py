"""Proxy and authorization contracts for knowledge governance."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.knowledge_governance import (
    review_governance_record,
    router,
)
from app.schemas.knowledge_governance import (
    KnowledgeGovernanceReviewRequest,
    KnowledgeReviewStatus,
)
from app.services.rag_client import rag_client


def test_proxy_router_exposes_management_endpoints() -> None:
    paths = {route.path for route in router.routes}

    assert "" in paths
    assert "/files/{file_id}" in paths
    assert "/{record_id}/submit" in paths
    assert "/{record_id}/review" in paths
    assert "/backfill" in paths


@pytest.mark.asyncio
async def test_admin_review_injects_authenticated_audit_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    record_id = uuid4()
    current_user = SimpleNamespace(
        project_id=project_id,
        username="admin-user",
        role="admin",
    )
    review = AsyncMock(
        return_value={
            "id": str(record_id),
            "project_id": str(project_id),
            "file_id": str(uuid4()),
            "qa_pair_id": None,
            "collection_id": str(uuid4()),
            "source_name": "manual.pdf",
            "document_type": "product",
            "product_line": "智能客服",
            "channels": ["wecom_kf"],
            "effective_at": "2026-07-17T00:00:00Z",
            "expires_at": None,
            "owner": "ops",
            "document_version": "v1",
            "allow_automatic_reply": True,
            "review_status": "approved",
            "reviewed_by": "admin-user",
            "reviewed_at": "2026-07-17T00:00:00Z",
            "source_origin": "internal",
            "content_is_untrusted": False,
            "created_at": "2026-07-17T00:00:00Z",
            "updated_at": "2026-07-17T00:00:00Z",
        }
    )
    monkeypatch.setattr(rag_client, "review_knowledge_governance", review)

    await review_governance_record(
        record_id=record_id,
        request=KnowledgeGovernanceReviewRequest(
            status=KnowledgeReviewStatus.APPROVED
        ),
        current_user=current_user,
    )

    review.assert_awaited_once()
    call = review.await_args.kwargs
    assert call["project_id"] == str(project_id)
    assert call["record_id"] == str(record_id)
    assert call["decision"]["reviewer"] == "admin-user"
    assert call["decision"]["status"] == "approved"
    assert call["decision"]["reviewed_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_non_admin_cannot_review() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await review_governance_record(
            record_id=uuid4(),
            request=KnowledgeGovernanceReviewRequest(
                status=KnowledgeReviewStatus.REJECTED
            ),
            current_user=SimpleNamespace(
                project_id=uuid4(),
                username="staff-user",
                role="user",
            ),
        )

    assert exc_info.value.status_code == 403
