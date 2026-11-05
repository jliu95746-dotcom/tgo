"""API errors retain actionable contracts without returning private inputs."""

import asyncio
import json
import logging
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
import httpx
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError
import pytest

from app.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    TGOAPIException,
    general_exception_handler,
    http_exception_handler,
    tgo_api_exception_handler,
    validation_exception_handler,
)
from app.core.logging import CustomJsonFormatter, StartupFormatter


MARKER = "synthetic-private-input-must-not-appear"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int
    attributes: dict[str, int] = {}


class CustomPayload(BaseModel):
    count: int

    @field_validator("count")
    @classmethod
    def reject(cls, value: int) -> int:
        raise PydanticCustomError(MARKER, MARKER)


def application() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(Exception, general_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    return app


def assert_private_error(response, caplog, *, status, code):
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    UUID(body["request_id"])
    assert MARKER not in response.text
    records = [item for item in caplog.records if item.name == "exceptions"]
    assert records
    assert MARKER not in repr([vars(item) for item in records])
    assert all(item.exc_info is None for item in records)
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [MARKER, {"private": MARKER}])
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_http_errors_hide_detail_and_preserve_retry_headers(
    caplog, detail, status
):
    app = application()

    @app.get("/provider/{identifier}")
    def failed(identifier: str):
        raise HTTPException(status, detail, headers={"Retry-After": "5"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/provider/" + MARKER)
    body = assert_private_error(
        response, caplog, status=status, code="HTTP_ERROR"
    )
    assert response.headers["retry-after"] == "5"
    assert body["error"]["details"] == {"status_code": status}


@pytest.mark.asyncio
@pytest.mark.parametrize("fatal_string", [False, True])
async def test_unknown_exceptions_never_format_raw_exception(
    caplog, fatal_string
):
    class PrivateError(RuntimeError):
        def __str__(self):
            if fatal_string:
                raise AssertionError("must not format private exceptions")
            return MARKER

    app = application()

    @app.get("/operation/{identifier}")
    def failed(identifier: str):
        raise PrivateError(MARKER)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/operation/" + MARKER)
    body = assert_private_error(
        response, caplog, status=500, code="INTERNAL_ERROR"
    )
    assert body["error"]["details"] is None


@pytest.mark.asyncio
async def test_external_service_errors_hide_provider_payload(caplog):
    app = application()

    @app.get("/operation/{identifier}")
    def failed(identifier: str):
        raise ExternalServiceError(
            "provider", MARKER, details={"credentials": MARKER}
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/operation/" + MARKER)
    body = assert_private_error(
        response, caplog, status=502, code="EXTERNAL_SERVICE_ERROR"
    )
    assert body["error"]["details"] is None


@pytest.mark.asyncio
async def test_controlled_conflicts_keep_business_details_and_serialize_uuid(
    caplog,
):
    app = application()
    business_id = uuid4()

    @app.get("/configuration/{identifier}")
    def failed(identifier: str):
        raise ConflictError(
            "Refresh the configuration", details={"current_id": business_id}
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/configuration/" + MARKER)
    body = assert_private_error(response, caplog, status=409, code="CONFLICT")
    assert body["error"]["message"] == "Refresh the configuration"
    assert body["error"]["details"]["current_id"] == str(business_id)

    request = Request({"type": "http", "method": "GET", "path": "/test"})
    result = await tgo_api_exception_handler(
        request, ConflictError("Refresh", details={"current_id": business_id})
    )
    assert json.loads(result.body)["error"]["details"]["current_id"] == str(
        business_id
    )


@pytest.mark.asyncio
async def test_authentication_errors_keep_the_challenge_header(caplog):
    caplog.set_level(logging.INFO, logger="exceptions")
    app = application()

    @app.get("/protected/{identifier}")
    def protected(identifier: str):
        raise HTTPException(
            401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/protected/" + MARKER)
    body = assert_private_error(
        response, caplog, status=401, code="HTTP_ERROR"
    )
    assert body["error"]["message"] == "Not authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_validation_errors_keep_declared_fields_but_no_raw_values(
    caplog,
):
    app = application()

    @app.post("/inputs/{identifier}")
    def inputs(identifier: str, body: Payload):
        return body

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/inputs/" + MARKER,
            json={"count": MARKER, "attributes": {MARKER: MARKER}, MARKER: 1},
        )
    body = assert_private_error(
        response, caplog, status=422, code="VALIDATION_ERROR"
    )
    errors = body["error"]["details"]["errors"]
    assert {tuple(item["loc"]) for item in errors} == {
        ("body", "count"),
        ("body", "attributes"),
        ("body",),
    }
    assert all(set(item) == {"type", "loc", "msg"} for item in errors)


@pytest.mark.asyncio
async def test_custom_validation_error_codes_cannot_copy_private_values(
    caplog,
):
    app = application()

    @app.post("/custom")
    def custom(body: CustomPayload):
        return body

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/custom", json={"count": 1})
    body = assert_private_error(
        response, caplog, status=422, code="VALIDATION_ERROR"
    )
    assert body["error"]["details"]["errors"][0]["type"] == "value_error"


@pytest.mark.asyncio
async def test_query_and_header_errors_keep_static_parameter_locations(caplog):
    app = application()

    @app.get("/queries")
    def inputs(count: int, x_count: int = Header()):
        return count + x_count

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/queries", params={"count": MARKER}, headers={"x-count": MARKER}
        )
    body = assert_private_error(
        response, caplog, status=422, code="VALIDATION_ERROR"
    )
    assert {
        tuple(item["loc"]) for item in body["error"]["details"]["errors"]
    } == {("query", "count"), ("header", "x-count")}


def test_existing_console_and_json_formats_keep_diagnostic_coordinates():
    record = logging.makeLogRecord(
        {
            "name": "exceptions",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "Unhandled Exception",
            "request_id": "synthetic-request",
            "route": "/inputs/{identifier}",
            "error_type": "RuntimeError",
        }
    )
    serialized = CustomJsonFormatter().format(record)
    assert json.loads(serialized)["request_id"] == "synthetic-request"
    assert "RuntimeError" in StartupFormatter().format(record)


@pytest.mark.asyncio
async def test_asgi_server_logs_hide_exception_text_and_keep_request_ids(
    caplog,
):
    async def failed_request():
        request = Request(
            {"type": "http", "method": "GET", "path": "/" + MARKER}
        )
        error = RuntimeError(MARKER)
        response = await general_exception_handler(request, error)
        await asyncio.sleep(0)
        try:
            raise error
        except RuntimeError as exc:
            logging.getLogger("uvicorn.error").error(
                "Exception in ASGI application\n", exc_info=exc
            )
        return json.loads(response.body)

    responses = await asyncio.gather(failed_request(), failed_request())
    records = [item for item in caplog.records if item.name == "uvicorn.error"]
    assert len(records) == 2
    assert MARKER not in repr([vars(item) for item in records])
    assert all(
        item.exc_info is None and item.levelno == logging.ERROR
        for item in records
    )
    assert {item.request_id for item in records} == {
        item["request_id"] for item in responses
    }
    assert len({item.request_id for item in records}) == 2


def test_other_server_log_events_are_preserved(caplog):
    logging.getLogger("uvicorn.error").error("Controlled operational event")
    record = next(
        item for item in caplog.records if item.name == "uvicorn.error"
    )
    assert record.getMessage() == "Controlled operational event"
    assert record.levelno == logging.ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 503])
async def test_internal_http_errors_keep_the_private_detail_contract(
    caplog, status
):
    from app.internal import internal_app

    caplog.set_level(logging.INFO, logger="exceptions")
    app = FastAPI()
    app.exception_handlers = internal_app.exception_handlers.copy()

    @app.get("/internal/operation/{identifier}")
    def failed(identifier: str):
        raise HTTPException(
            status,
            MARKER if status == 503 else "Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/internal/operation/" + MARKER)
    assert response.status_code == status
    assert set(response.json()) == {"detail"}
    assert MARKER not in response.text
    assert response.headers["www-authenticate"] == "Bearer"
    UUID(response.headers["x-request-id"])
    records = [item for item in caplog.records if item.name == "exceptions"]
    assert records and MARKER not in repr([vars(item) for item in records])
