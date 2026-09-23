"""Cancellation and AI analysis obey the same ownership boundary as chat history."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api.v1.endpoints import ai_runs, message_analysis
from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.ai_runs import ReplyRun, StaffCancelRequest
from app.schemas.message_analysis import StaffMessageAnalysisBatchRequest


@pytest.mark.asyncio
async def test_other_staff_reply_cannot_be_cancelled(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    actor = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="user")
    item = ReplyRun(
        project_id=str(actor.project_id),
        client_msg_no="synthetic",
        channel_id=f"{uuid4()}-vtr",
        channel_type=251,
    )
    cancel = AsyncMock()
    monkeypatch.setattr(ai_runs, "get_reply_run", AsyncMock(return_value=item))
    monkeypatch.setattr(ai_runs, "cancel_reply", cancel)
    db = Mock()
    db.scalar.return_value = None
    with pytest.raises(TGOAPIException) as error:
        await ai_runs.cancel_run_by_staff(
            StaffCancelRequest(client_msg_no="synthetic"), actor, db
        )
    assert error.value.status_code == 404
    cancel.assert_not_awaited()


def test_unowned_message_analysis_is_rejected_before_loading(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    actor = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="user")
    request = StaffMessageAnalysisBatchRequest(
        messages=[{"channel_id": f"{uuid4()}-vtr", "source_message_id": "synthetic"}]
    )
    service = Mock()
    monkeypatch.setattr(message_analysis, "MessageAnalysisService", service)
    db = Mock()
    db.scalar.return_value = None
    with pytest.raises(TGOAPIException) as error:
        message_analysis.get_staff_message_analyses(request, actor, db)
    assert error.value.status_code == 404
    service.assert_not_called()
