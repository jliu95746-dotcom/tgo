"""Regression tests for internal AI event ingestion."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.api.internal.endpoints import ai_events
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
            payload={"reason": "customer requested human support"},
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
