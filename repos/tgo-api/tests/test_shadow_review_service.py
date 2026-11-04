"""Shadow cases pair only confirmed replies for the same customer."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi import Response

from app.api.v1.endpoints import message_analysis
from app.core.exceptions import TGOAPIException
from app.models.message_intent_result import MessageIntentResult
from app.models.assist_draft_record import AssistDraftRecord
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.shadow_review import ShadowReviewState
from app.services.shadow_review_service import pair_shadow_cases


def test_shadow_pairing_excludes_other_customer_and_unconfirmed_delivery() -> (
    None
):
    project_id, visitor_id, other_visitor_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    intent = MessageIntentResult(
        id=uuid4(),
        project_id=project_id,
        platform_id=uuid4(),
        visitor_id=visitor_id,
        source_message_id="customer-1",
        analysis_mode="shadow",
        intent="product_inquiry",
        confidence=0.96,
        risk_level="low",
        recommended_route="auto_reply",
        need_human=False,
        created_at=now,
    )

    def reply(
        visitor: object, status: str, history: str, content: str, kind: int = 1
    ) -> StaffMessageDelivery:
        return StaffMessageDelivery(
            id=uuid4(),
            project_id=project_id,
            visitor_id=visitor,
            source_message_id="customer-1",
            client_msg_no=f"staff-{uuid4()}",
            payload={"type": kind, "content": content},
            external_status=status,
            history_status=history,
            created_at=now,
        )

    results = pair_shadow_cases(
        project_id,
        visitor_id,
        [intent],
        [
            reply(other_visitor_id, "sent", "sent", "别的客户回复"),
            reply(visitor_id, "unknown", "pending", "投递未确认"),
            reply(visitor_id, "not_required", "sent", "人工已回复"),
            reply(visitor_id, "not_required", "sent", "人工附图回复", 12),
        ],
    )

    assert len(results) == 1
    assert results[0].source_message_id == "customer-1"
    assert [item.content for item in results[0].replies] == [
        "人工已回复",
        "人工附图回复",
    ]


def test_shadow_pairing_shows_only_matching_server_saved_draft() -> None:
    project_id, visitor_id, staff_id, agent_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    source_id = "customer-source"
    intent = MessageIntentResult(
        project_id=project_id,
        visitor_id=visitor_id,
        platform_id=uuid4(),
        source_message_id=source_id,
        analysis_mode="shadow",
        intent="product_inquiry",
        confidence=0.9,
        risk_level="low",
        recommended_route="auto_reply",
        need_human=False,
        created_at=now,
    )
    draft = AssistDraftRecord(
        id=uuid4(),
        project_id=project_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        source_message_id=source_id,
        response_data={
            "draft": "AI 建议回复",
            "generation_trace": [
                {"phase": "fact_answer", "model_name": "openai:gpt-4o",
                 "configured_prompt_sha256": "a" * 64,
                 "requested_agent_id": str(agent_id),
                 "request_system_prompt_sha256": "b" * 64},
            ],
        },
        created_at=now,
    )
    wrong_customer_draft = AssistDraftRecord(
        id=uuid4(),
        project_id=project_id,
        visitor_id=uuid4(),
        staff_id=staff_id,
        source_message_id=source_id,
        response_data={"draft": "其他客户的内容"},
        created_at=now,
    )
    deliveries = [
        StaffMessageDelivery(
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
            source_message_id=source_id,
            assist_draft_id=draft.id,
            client_msg_no="reply-1",
            payload={"type": 1, "content": "员工实际回复"},
            external_status="not_required",
            history_status="sent",
            created_at=now,
        ),
        StaffMessageDelivery(
            project_id=project_id,
            visitor_id=visitor_id,
            staff_id=staff_id,
            source_message_id=source_id,
            assist_draft_id=wrong_customer_draft.id,
            client_msg_no="reply-2",
            payload={"type": 1, "content": "另一次人工回复"},
            external_status="not_required",
            history_status="sent",
            created_at=now,
        ),
    ]

    comparisons = pair_shadow_cases(
        project_id,
        visitor_id,
        [intent],
        deliveries,
        [draft, wrong_customer_draft],
        {
            draft.id: ShadowReviewState(
                verdict="severe_error",
                reason_code="wrong_fact",
                reviewed_at=now,
            )
        },
    )

    assert comparisons[0].replies[0].ai_draft == "AI 建议回复"
    assert comparisons[0].replies[0].ai_draft_id == draft.id
    assert (
        comparisons[0].replies[0].generation_trace[0].model_name
        == "openai:gpt-4o"
    )
    assert comparisons[0].replies[0].generation_trace[0].requested_agent_id == agent_id
    assert comparisons[0].replies[0].generation_trace[0].request_system_prompt_sha256 == "b" * 64
    assert comparisons[0].replies[0].review.verdict == "severe_error"
    assert comparisons[0].replies[1].ai_draft is None
    assert comparisons[0].replies[1].generation_trace == ()
    assert comparisons[0].replies[1].review is None


def test_shadow_cases_endpoint_rejects_unowned_visitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import staff_conversation_scope

    monkeypatch.setattr(
        staff_conversation_scope.settings, "SAAS_BILLING_ENABLED", True
    )
    db = MagicMock()
    db.scalar.return_value = None
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="agent")

    with pytest.raises(TGOAPIException) as error:
        message_analysis.get_staff_shadow_cases(
            visitor_id=uuid4(),
            response=Response(),
            limit=30,
            current_user=staff,
            db=db,  # type: ignore[arg-type]
        )

    assert error.value.status_code == 404


def test_shadow_cases_endpoint_disables_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import staff_conversation_scope

    monkeypatch.setattr(
        staff_conversation_scope.settings, "SAAS_BILLING_ENABLED", False
    )
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    visitor_id = uuid4()
    focus_draft_id = uuid4()
    db = MagicMock()
    service = MagicMock()
    expected = SimpleNamespace(items=())
    service.list_comparisons.return_value = expected
    monkeypatch.setattr(
        message_analysis, "ShadowReviewService", lambda _db: service
    )
    response = Response()

    result = message_analysis.get_staff_shadow_cases(
        visitor_id=visitor_id,
        response=response,
        limit=30,
        focus_draft_id=focus_draft_id,
        current_user=staff,
        db=db,  # type: ignore[arg-type]
    )

    assert result is expected
    assert response.headers["Cache-Control"] == "no-store"
    service.list_comparisons.assert_called_once_with(
        staff.project_id, visitor_id, staff.id, 30,
        focus_draft_id=focus_draft_id,
    )


def test_shadow_queue_endpoint_disables_caching_and_scopes_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    service = MagicMock()
    expected = SimpleNamespace(items=())
    service.pending_queue.return_value = expected
    monkeypatch.setattr(
        message_analysis, "ShadowReviewAudit", lambda _db: service
    )
    response = Response()

    result = message_analysis.get_staff_shadow_review_queue(
        response=response,
        days=7,
        limit=10,
        after_draft_id=None,
        current_user=staff,
        db=MagicMock(),  # type: ignore[arg-type]
    )

    assert result is expected
    assert response.headers["Cache-Control"] == "no-store"
    service.pending_queue.assert_called_once_with(
        staff.project_id,
        staff.id,
        days=7,
        limit=10,
        after_draft_id=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", [
        "/staff/shadow-reviews",
        "/staff/shadow-review-summary",
        "/staff/shadow-review-queue",
    ]
)
async def test_shadow_review_write_and_summary_require_admin(
    path: str,
) -> None:
    route = next(
        route for route in message_analysis.router.routes if route.path == path
    )
    admin_check = next(
        dependency.call
        for dependency in route.dependant.dependencies
        if dependency.name == "current_user"
    )
    with pytest.raises(HTTPException) as denied:
        await admin_check(SimpleNamespace(role="agent", username="staff"))
    assert denied.value.status_code == 403
    admin = SimpleNamespace(role="admin", username="reviewer")
    assert await admin_check(admin) is admin
