"""WuKongIM visitor messages honor assist and manual service modes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints import wukongim_webhook
from app.models import Platform, Project, Visitor
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_mode", "expected_shadow_calls"),
    (("assist", [True]), ("manual", []), ("auto", [False])),
)
async def test_wukongim_message_analysis_respects_service_mode(
    monkeypatch: pytest.MonkeyPatch,
    service_mode: str,
    expected_shadow_calls: list[bool],
) -> None:
    project = SimpleNamespace(id=uuid4())
    platform = SimpleNamespace(id=uuid4(), ai_mode="auto")
    visitor = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        platform_id=platform.id,
        service_mode=service_mode,
        ai_disabled=service_mode != "auto",
    )
    db = MagicMock()

    def query(model: type[object]) -> MagicMock:
        result = MagicMock()
        result.filter.return_value = result
        result.first.return_value = {
            Visitor: visitor,
            Platform: platform,
            Project: project,
        }[model]
        return result

    db.query.side_effect = query
    db.execute.return_value.rowcount = 1
    calls: list[bool] = []

    class _IntentOrchestrator:
        async def analyze_text_message(self, **kwargs: object) -> None:
            calls.append(kwargs["shadow_only"] is True)

    monkeypatch.setattr(
        wukongim_webhook,
        "CustomerLogisticsService",
        lambda _db: SimpleNamespace(capture_message=lambda **_kwargs: None),
    )
    monkeypatch.setattr(
        wukongim_webhook,
        "MessageAnalysisService",
        lambda _db: SimpleNamespace(
            get_combined_results_for_project=lambda **_kwargs: []
        ),
    )
    monkeypatch.setattr(
        wukongim_webhook, "MessageIntentOrchestrator", lambda _db: _IntentOrchestrator()
    )

    await wukongim_webhook._handle_msg_notify_batch(
        [
            {
                "from_uid": f"{visitor.id}-vtr",
                "channel_id": build_visitor_channel_id(visitor.id),
                "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE,
                "client_msg_no": "shadow-message-1",
                "message_seq": 1,
                "payload": {"type": 1, "content": "请帮我查订单"},
            }
        ],
        db,
    )

    assert calls == expected_shadow_calls


@pytest.mark.asyncio
async def test_existing_wecom_analysis_still_generates_hidden_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(id=uuid4())
    platform = SimpleNamespace(id=uuid4(), type="wecom", agent_id=uuid4(), ai_mode="auto")
    visitor = SimpleNamespace(
        id=uuid4(), project_id=project.id, platform_id=platform.id,
        service_mode="assist", ai_disabled=True,
    )
    db = MagicMock()

    def query(model: type[object]) -> MagicMock:
        result = MagicMock()
        result.filter.return_value = result
        result.first.return_value = {
            Visitor: visitor, Platform: platform, Project: project,
        }[model]
        return result

    db.query.side_effect = query
    db.execute.return_value.rowcount = 1
    candidate_calls: list[dict[str, object]] = []

    class _HiddenCandidate:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def generate(self, **kwargs: object) -> None:
            candidate_calls.append(kwargs)

    monkeypatch.setattr(
        wukongim_webhook, "CustomerLogisticsService",
        lambda _db: SimpleNamespace(capture_message=lambda **_kwargs: None),
    )
    monkeypatch.setattr(
        wukongim_webhook, "MessageAnalysisService",
        lambda _db: SimpleNamespace(
            get_combined_results_for_project=lambda **_kwargs: [object()]
        ),
    )
    monkeypatch.setattr(wukongim_webhook, "HiddenShadowRunService", _HiddenCandidate)

    await wukongim_webhook._handle_msg_notify_batch(
        [{
            "from_uid": f"{visitor.id}-vtr",
            "channel_id": build_visitor_channel_id(visitor.id),
            "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE,
            "client_msg_no": "shadow-message-1",
            "message_seq": 1,
            "payload": {"type": 1, "content": "请帮我查订单"},
        }], db,
    )

    assert len(candidate_calls) == 1
    assert candidate_calls[0]["source_message_id"] == "shadow-message-1"
    assert candidate_calls[0]["agent_id"] == platform.agent_id
