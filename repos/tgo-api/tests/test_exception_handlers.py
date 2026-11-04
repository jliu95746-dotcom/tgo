"""Tests for API exception handlers."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ValidationError as PydanticValidationError

from app.core.exceptions import validation_exception_handler
from app.schemas.operations import OperatorLoginRequest
from app.schemas.device_script_publication import (
    ScriptPolicyPublish,
    ScriptPolicyRevoke,
)


class _ValidationPayload(BaseModel):
    user_id: str


class ExceptionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_script_validation_never_echoes_rejected_input(self) -> None:
        marker = "private-script-validation-marker"
        publication = {
            "expected_policy_revision": 0,
            "connection_instance_id": "c" * 32,
            "session_id": "d" * 32,
            "script_id": "daily_report",
            "grant_digest": "a" * 64,
            "timeout_seconds": 10,
            "max_output_bytes": 4096,
            "max_runs": 1,
            "command": {"private": marker},
        }
        for schema, body, suffix in (
            (ScriptPolicyPublish, publication, "script-policy"),
            (
                ScriptPolicyRevoke,
                {"expected_policy_revision": 1, "command": marker},
                "script-policy/revoke",
            ),
        ):
            try:
                schema.model_validate(body)
            except PydanticValidationError as exc:
                request = Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": (
                            "/v1/device-control/devices/"
                            f"{uuid4()}/{suffix}"
                        ),
                        "headers": [],
                    }
                )
                with patch("app.core.exceptions.logger.warning") as warning:
                    response = await validation_exception_handler(
                        request, exc
                    )
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(marker, response.body.decode())
                self.assertNotIn(marker, str(warning.call_args))
            else:
                self.fail("Unreviewed execution fields must be rejected")

    async def test_operator_validation_never_echoes_credentials(self) -> None:
        secret_marker = "credential-must-not-appear"
        try:
            OperatorLoginRequest.model_validate(
                {
                    "email": "invalid",
                    "password": {"value": secret_marker},
                    "unexpected": {"password": secret_marker},
                }
            )
        except PydanticValidationError as exc:
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/v1/ops/login",
                    "headers": [],
                }
            )
            with patch("app.core.exceptions.logger.warning") as warning:
                response = await validation_exception_handler(request, exc)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(secret_marker, response.body.decode())
            self.assertNotIn(secret_marker, str(warning.call_args))
        else:
            self.fail("Expected invalid credentials to fail validation")

    async def test_validation_exception_handler_serializes_uuid_inputs(
        self,
    ) -> None:
        """Validation responses should stay JSON-serializable."""
        invalid_user_id = uuid4()

        try:
            _ValidationPayload(user_id=invalid_user_id)
        except PydanticValidationError as exc:
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/internal/ai/events",
                    "headers": [],
                    "query_string": b"",
                    "client": ("testclient", 123),
                    "server": ("testserver", 80),
                    "scheme": "http",
                    "root_path": "",
                    "http_version": "1.1",
                }
            )
            response = await validation_exception_handler(request, exc)
        else:
            self.fail("Expected a validation error for UUID input")

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            body["error"]["details"]["errors"][0]["input"],
            str(invalid_user_id),
        )
