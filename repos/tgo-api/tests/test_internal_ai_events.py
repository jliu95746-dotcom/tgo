"""Regression tests for internal AI event ingestion."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.internal.endpoints import ai_events
from app.core.config import settings
from app.core.database import get_db
from app.schemas.ai import AIServiceEvent


class _FakeQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    def filter(self, *_args: object, **_kwargs: object) -> _FakeQuery:
        return self

    def first(self) -> object:
        return self._result


class _FakeDB:
    def __init__(self, visitor: object, project: object) -> None:
        self._results = {
            ai_events.Visitor: visitor,
            ai_events.Project: project,
        }

    def query(self, model: object) -> _FakeQuery:
        return _FakeQuery(self._results[model])


@pytest.mark.asyncio
async def test_authenticated_ai_event_reaches_the_handoff_handler() -> None:
    project_id, visitor_id = uuid4(), uuid4()
    visitor = SimpleNamespace(id=visitor_id, project_id=project_id)
    project = SimpleNamespace(id=project_id)
    app = FastAPI()
    app.include_router(ai_events.router, prefix="/internal/ai/events")
    app.dependency_overrides[get_db] = lambda: _FakeDB(visitor, project)
    handler = AsyncMock(return_value={"status": "pending"})
    with patch.object(ai_events, "_handle_manual_service_request", handler):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/internal/ai/events",
                json={
                    "event_type": "manual_service.request",
                    "user_id": str(visitor_id),
                    "payload": {"reason": "需要人工客服"},
                },
                headers={"X-Internal-API-Key": settings.SECRET_KEY},
            )
    assert response.status_code == 202
    assert response.json() == {
        "event_type": "manual_service.request",
        "result": {"status": "pending"},
    }
    handler.assert_awaited_once()


class InternalAIEventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_ai_event_normalizes_user_id_to_string(
        self,
    ) -> None:
        """Internal ingestion should keep the canonical user_id as a string."""
        visitor_id = uuid4()
        project_id = uuid4()
        visitor = SimpleNamespace(
            id=visitor_id,
            project_id=project_id,
            deleted_at=None,
        )
        project = SimpleNamespace(id=project_id, deleted_at=None)
        db = _FakeDB(visitor=visitor, project=project)
        event = AIServiceEvent(
            event_type="user_info.update",
            user_id=str(visitor_id),
            payload={},
        )

        handler = AsyncMock(return_value={"ok": True})
        with patch.object(ai_events, "_handle_visitor_info_update", handler):
            result = await ai_events.ingest_ai_event_internal(
                event=event,
                db=db,
            )

        self.assertEqual(
            result,
            {"event_type": "user_info.update", "result": {"ok": True}},
        )
        self.assertEqual(event.user_id, str(visitor_id))
        self.assertEqual(handler.await_args.args[0].user_id, str(visitor_id))

    async def test_manual_handoff_disables_ai_for_already_assigned_visitor(
        self,
    ) -> None:
        """An active visitor must be handed to their assigned staff, not fake-queued."""
        visitor_id = uuid4()
        project_id = uuid4()
        staff_id = uuid4()
        session_id = uuid4()
        visitor = SimpleNamespace(
            id=visitor_id,
            project_id=project_id,
            deleted_at=None,
            is_unassigned=False,
            service_status="active",
            ai_disabled=None,
        )
        project = SimpleNamespace(id=project_id)
        db = _FakeDB(visitor=visitor, project=project)
        event = AIServiceEvent(
            event_type="manual_service.request",
            user_id=str(visitor_id),
            payload={
                "reason": "customer requested human support",
                "metadata": {"request_id": "turn-123"},
            },
        )
        handoff_result = {
            "assigned_staff_id": str(staff_id),
            "session_id": str(session_id),
            "status": "active",
        }

        with patch.object(
            ai_events,
            "request_human_handoff",
            new=AsyncMock(return_value=handoff_result),
        ) as request_handoff:
            result = await ai_events._handle_manual_service_request(
                event=event,
                project=project,
                db=db,
            )

        self.assertEqual(result, handoff_result)
        request_handoff.assert_awaited_once()
        self.assertEqual(
            request_handoff.await_args.kwargs["reason"],
            "customer requested human support",
        )
        self.assertEqual(
            request_handoff.await_args.kwargs["source_message_id"], "ai:turn-123",
        )
        self.assertIs(request_handoff.await_args.kwargs["strict_source_receipt"], True)

    async def test_manual_handoff_requires_visitor_id(
        self,
    ) -> None:
        """The internal event cannot create an anonymous handoff request."""
        project_id = uuid4()
        project = SimpleNamespace(id=project_id)
        db = _FakeDB(visitor=None, project=project)
        event = AIServiceEvent(
            event_type="manual_service.request",
            user_id=None,
            payload={"reason": "customer requested human support"},
        )

        with self.assertRaises(ai_events.HTTPException) as raised:
            await ai_events._handle_manual_service_request(
                event=event,
                project=project,
                db=db,
            )

        self.assertEqual(raised.exception.status_code, 400)

    async def test_manual_handoff_rejects_invalid_request_id(self) -> None:
        visitor_id = uuid4()
        project_id = uuid4()
        visitor = SimpleNamespace(id=visitor_id, project_id=project_id)
        project = SimpleNamespace(id=project_id)
        db = _FakeDB(visitor=visitor, project=project)
        event = AIServiceEvent(
            event_type="manual_service.request",
            user_id=str(visitor_id),
            payload={"reason": "转人工", "metadata": {"request_id": 123}},
        )
        with patch.object(ai_events, "request_human_handoff") as handoff:
            with self.assertRaises(ai_events.HTTPException) as raised:
                await ai_events._handle_manual_service_request(event, project, db)
        self.assertEqual(raised.exception.status_code, 400)
        handoff.assert_not_called()
