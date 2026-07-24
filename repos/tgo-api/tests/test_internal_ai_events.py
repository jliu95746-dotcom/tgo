"""Regression tests for internal AI event ingestion."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
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


class _ManualServiceDB:
    def __init__(self, visitor: object, active_session: object) -> None:
        self.visitor = visitor
        self.active_session = active_session
        self.commits = 0

    def query(self, model: object) -> _FakeQuery:
        if model is ai_events.Visitor:
            return _FakeQuery(self.visitor)
        if model is ai_events.VisitorWaitingQueue:
            return _FakeQuery(None)
        if model is ai_events.VisitorSession:
            return _FakeQuery(self.active_session)
        raise AssertionError(f"Unexpected model query: {model}")

    def commit(self) -> None:
        self.commits += 1


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
        active_session = SimpleNamespace(
            id=session_id,
            staff_id=staff_id,
            status="open",
        )
        project = SimpleNamespace(id=project_id)
        db = _ManualServiceDB(visitor=visitor, active_session=active_session)
        event = AIServiceEvent(
            event_type="manual_service.request",
            user_id=str(visitor_id),
            payload={"reason": "customer requested human support"},
        )

        with (
            patch.object(ai_events, "_ensure_manual_service_tag"),
            patch.object(
                ai_events,
                "notify_visitor_profile_updated",
                new=AsyncMock(),
            ) as notify,
        ):
            result = await ai_events._handle_manual_service_request(
                event=event,
                project=project,
                db=db,
            )

        self.assertTrue(visitor.ai_disabled)
        self.assertEqual(result["assigned_staff_id"], str(staff_id))
        self.assertEqual(result["session_id"], str(session_id))
        self.assertEqual(result["status"], "active")
        self.assertGreaterEqual(db.commits, 2)
        notify.assert_awaited_once_with(db, visitor)

    async def test_manual_handoff_rejects_transfer_without_human_state(
        self,
    ) -> None:
        """A nominal transfer result cannot be reported as success without state."""
        visitor_id = uuid4()
        project_id = uuid4()
        visitor = SimpleNamespace(
            id=visitor_id,
            project_id=project_id,
            deleted_at=None,
            is_unassigned=True,
            service_status="unassigned",
            ai_disabled=None,
        )
        project = SimpleNamespace(id=project_id)
        db = _ManualServiceDB(visitor=visitor, active_session=None)
        event = AIServiceEvent(
            event_type="manual_service.request",
            user_id=str(visitor_id),
            payload={"reason": "customer requested human support"},
        )
        transfer_result = SimpleNamespace(
            success=True,
            assigned_staff_id=None,
            session=None,
            waiting_queue=None,
            message="transfer completed",
        )

        with (
            patch.object(ai_events, "_ensure_manual_service_tag"),
            patch.object(
                ai_events,
                "transfer_to_staff",
                new=AsyncMock(return_value=transfer_result),
            ),
        ):
            with self.assertRaises(ai_events.HTTPException) as raised:
                await ai_events._handle_manual_service_request(
                    event=event,
                    project=project,
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("no staff session or waiting queue", raised.exception.detail)
