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


class _ValidationPayload(BaseModel):
    user_id: str


class ExceptionHandlerTests(unittest.IsolatedAsyncioTestCase):
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
