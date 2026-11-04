from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from typing import Any
import unittest
from unittest.mock import patch

import httpx

from app.config import settings

_BASE_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "runtime" / "tools" / "custom" / "base.py"
)
_BASE_MODULE_SPEC = spec_from_file_location(
    "test_custom_tool_base_module",
    _BASE_MODULE_PATH,
)
assert _BASE_MODULE_SPEC is not None
assert _BASE_MODULE_SPEC.loader is not None
_BASE_MODULE = module_from_spec(_BASE_MODULE_SPEC)
sys.modules[_BASE_MODULE_SPEC.name] = _BASE_MODULE
_BASE_MODULE_SPEC.loader.exec_module(_BASE_MODULE)

EventClient = _BASE_MODULE.EventClient
ToolContext = _BASE_MODULE.ToolContext


class _DummyResponse:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        return {}


class _DummyAsyncClient:
    def __init__(self, captured: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        self.captured = captured
        self.captured["client_options"] = kwargs

    async def __aenter__(self) -> _DummyAsyncClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> _DummyResponse:
        self.captured["url"] = url
        self.captured["json"] = json
        self.captured["headers"] = headers
        return _DummyResponse()


class EventClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_without_request_id_does_not_submit(self) -> None:
        captured: dict[str, Any] = {}
        with (
            patch.object(settings, "api_internal_service_url", "http://internal.test"),
            patch.object(settings, "secret_key", "test-internal-key"),
            patch.object(httpx, "AsyncClient",
                         lambda *args, **kwargs: _DummyAsyncClient(captured)),
        ):
            client = EventClient(ToolContext(
                agent_id="agent-1", session_id="session-1", user_id="visitor-1",
            ))
            result = await client.post_event(
                "manual_service.request", {"reason": "转人工"},
                error_messages={
                    "not_configured": "not_configured", "api_error": "api_error",
                    "http_error": "http_error", "unexpected_error": "unexpected_error",
                },
            )
        self.assertFalse(result.success)
        self.assertEqual(captured, {})

    async def test_handoff_uses_request_id_and_returns_committed_receipt(self) -> None:
        captured: dict[str, Any] = {}
        with (
            patch.object(settings, "api_internal_service_url", "http://internal.test"),
            patch.object(settings, "secret_key", "test-internal-key"),
            patch.object(httpx, "AsyncClient",
                         lambda *args, **kwargs: _DummyAsyncClient(captured)),
            patch.object(_DummyResponse, "json", return_value={
                "event_type": "manual_service.request",
                "result": {
                    "request_id": "handoff-1", "status": "active",
                    "notification_status": "unknown",
                },
            }),
        ):
            client = EventClient(ToolContext(
                agent_id="agent-1", session_id="session-1",
                user_id="visitor-1", request_id="turn-123",
            ))
            result = await client.post_event(
                "manual_service.request", {"reason": "转人工"},
                error_messages={
                    "not_configured": "not_configured", "api_error": "api_error",
                    "http_error": "http_error", "unexpected_error": "unexpected_error",
                },
            )
        self.assertTrue(result.success)
        self.assertEqual(result.request_id, "handoff-1")
        self.assertEqual(result.notification_status, "unknown")
        self.assertEqual(
            captured["json"]["payload"]["metadata"]["request_id"], "turn-123",
        )

    async def test_rejected_staff_operations_are_not_reported_as_saved(self) -> None:
        captured: dict[str, Any] = {}
        with patch.object(settings, "api_internal_service_url", "http://internal.test"), patch.object(
            httpx, "AsyncClient", lambda *args, **kwargs: _DummyAsyncClient(captured),
        ):
            client = EventClient(ToolContext(
                agent_id="fixture-agent", session_id="fixture-session",
                user_id="fixture-staff", project_id="fixture-project",
            ))
            for status_code in (302, 400, 404, 503):
                with self.subTest(status_code=status_code), patch.object(
                    _DummyResponse, "status_code", status_code,
                ):
                    result = await client.post_event(
                        "user_info.update", {"visitor": {"name": "fixture"}},
                        error_messages={
                            "not_configured": "not_configured", "api_error": "not_saved",
                            "http_error": "http_error", "unexpected_error": "unexpected_error",
                        },
                    )
                    self.assertFalse(result.success)
                    self.assertEqual(result.message, "not_saved")

    async def test_post_event_uses_configured_internal_api_url(self) -> None:
        captured: dict[str, Any] = {}
        original_internal_url = settings.api_internal_service_url
        original_api_service_url = settings.api_service_url
        settings.api_internal_service_url = "http://tgo-api:8001"
        settings.api_service_url = "http://tgo-api:8000"
        self.addCleanup(
            setattr,
            settings,
            "api_internal_service_url",
            original_internal_url,
        )
        self.addCleanup(
            setattr,
            settings,
            "api_service_url",
            original_api_service_url,
        )

        client = EventClient(
            ToolContext(
                agent_id="agent-1",
                session_id="session-1",
                user_id="visitor-1",
                project_id="project-1",
            )
        )

        with patch.object(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: _DummyAsyncClient(captured, *args, **kwargs),
        ):
            result = await client.post_event(
                "user_sentiment.update",
                {"sentiment": {"satisfaction": 5}},
                error_messages={
                    "not_configured": "not_configured",
                    "api_error": "api_error",
                    "http_error": "http_error",
                    "unexpected_error": "unexpected_error",
                },
            )

        self.assertTrue(result.success)
        self.assertEqual(
            captured["url"],
            "http://tgo-api:8001/internal/ai/events",
        )
        self.assertEqual(
            captured["headers"]["X-Internal-API-Key"], settings.secret_key,
        )
        self.assertIs(captured["client_options"]["trust_env"], False)
        self.assertIs(captured["client_options"]["follow_redirects"], False)

    async def test_missing_internal_url_never_falls_back_to_public_api(self) -> None:
        captured: dict[str, Any] = {}
        with patch.object(settings, "api_internal_service_url", ""), patch.object(
            settings, "api_service_url", "http://public.test"
        ), patch.object(
            httpx, "AsyncClient", lambda *args, **kwargs: _DummyAsyncClient(captured)
        ):
            client = EventClient(ToolContext(
                agent_id="agent-1", session_id="session-1", user_id="visitor-1",
            ))
            result = await client.post_event(
                "manual_service.request", {"reason": "转人工"},
                error_messages={
                    "not_configured": "not_configured", "api_error": "api_error",
                    "http_error": "http_error", "unexpected_error": "unexpected_error",
                },
            )
        self.assertFalse(result.success)
        self.assertEqual(result.message, "not_configured")
        self.assertEqual(captured, {})
