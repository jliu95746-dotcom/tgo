"""RAG diagnostics keep request values and exception bodies private."""

import asyncio
import http.client
import logging
import socket
import threading
import time
from types import SimpleNamespace
import traceback
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr, field_validator
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse
import uvicorn

from src.rag_service import main
from src.rag_service.config import Settings
from src.rag_service.logging_config import request_id_ctx
from src.rag_service.services import service_auth
from src.rag_service.services.http_diagnostics import (
    HTTPErrorPrivacyMiddleware,
)


PRIVATE = "fixture-private-request-value"


class NumberBody(BaseModel):
    value: int


class CheckedBody(BaseModel):
    value: str

    @field_validator("value")
    @classmethod
    def reject_value(cls, value: str) -> str:
        raise ValueError(f"Rejected private value: {value}")


@pytest.fixture
def boundary_app(monkeypatch):
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "disabled", False)
    logger = Mock()
    monkeypatch.setattr(main, "logger", logger)
    app = FastAPI()
    settings = Settings(
        _env_file=None,
        environment="test",
        metrics_enabled=False,
        saas_enabled=False,
    )
    main.setup_middleware(app, settings)
    main.setup_exception_handlers(app)

    @app.get("/fixture/items/{item_id}")
    async def get_item(item_id: str):
        return {"item_id": item_id, "ok": True}

    @app.post("/fixture/numbers")
    async def number(payload: NumberBody):
        return {"value": payload.value}

    @app.post("/fixture/checked")
    async def checked(payload: CheckedBody):
        return {"value": payload.value}

    client = TestClient(app, raise_server_exceptions=False)
    yield SimpleNamespace(app=app, client=client, logger=logger)
    client.close()


def assert_private_diagnostics(case, capsys) -> None:
    captured = capsys.readouterr()
    assert PRIVATE not in repr(case.logger.mock_calls)
    assert PRIVATE not in captured.out + captured.err
    assert "[DEBUG]" not in captured.out + captured.err


def test_success_keeps_response_and_tracking_without_logging_values(
    boundary_app,
    capsys,
):
    case = boundary_app
    response = case.client.get(
        f"/fixture/items/{PRIVATE}?token={PRIVATE}",
        headers={"user-agent": PRIVATE},
    )
    assert response.status_code == 200
    assert response.json() == {"item_id": PRIVATE, "ok": True}
    assert float(response.headers["X-Process-Time"]) >= 0
    assert response.headers["X-Request-ID"]
    assert request_id_ctx.get() is None
    assert "/fixture/items/{item_id}" in repr(case.logger.mock_calls)
    assert_private_diagnostics(case, capsys)


def test_unrecognized_method_is_bounded_in_diagnostics(boundary_app, capsys):
    case = boundary_app
    response = case.client.request(PRIVATE, "/fixture/items/known")
    assert response.status_code == 405
    assert "OTHER" in repr(case.logger.mock_calls)
    assert_private_diagnostics(case, capsys)


def test_unknown_route_does_not_log_arbitrary_path_or_query(
    boundary_app,
    capsys,
):
    case = boundary_app
    response = case.client.get(f"/unknown/{PRIVATE}?token={PRIVATE}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert_private_diagnostics(case, capsys)


@pytest.mark.parametrize(
    "exception_type", [HTTPException, StarletteHTTPException]
)
@pytest.mark.parametrize(
    "status,header,value,detail",
    [
        (401, "WWW-Authenticate", "Bearer", "Authentication required"),
        (429, "Retry-After", "12", {"reason": "Please retry later"}),
    ],
)
def test_http_errors_keep_public_details_status_and_protocol_headers(
    boundary_app,
    capsys,
    exception_type,
    status,
    header,
    value,
    detail,
):
    case = boundary_app

    @case.app.get("/fixture/http-error")
    async def fail():
        raise exception_type(status, detail, headers={header: value})

    response = case.client.get(f"/fixture/http-error?token={PRIVATE}")
    assert response.status_code == status
    body = response.json()["error"]
    assert body["message"] == detail
    assert body["details"] == {"status_code": status}
    assert response.headers[header] == value
    assert all(
        "detail" not in call.kwargs
        for call in case.logger.warning.call_args_list
    )
    assert_private_diagnostics(case, capsys)


@pytest.mark.parametrize(
    "error_class,status", [(ValueError, 400), (RuntimeError, 500)]
)
@pytest.mark.parametrize("hostile_string", [False, True])
def test_failures_do_not_render_exception_values_or_tracebacks(
    boundary_app,
    capsys,
    error_class,
    status,
    hostile_string,
):
    case = boundary_app
    rendered = []

    class PrivateFailure(error_class):
        def __str__(self):
            rendered.append(True)
            if hostile_string:
                raise AssertionError("Error formatter must not run")
            return PRIVATE

    @case.app.get("/fixture/failure")
    async def fail():
        raise PrivateFailure(PRIVATE)

    response = case.client.get(f"/fixture/failure?token={PRIVATE}")
    assert response.status_code == status
    body = response.json()["error"]
    assert body["code"] == (
        "VALIDATION_ERROR" if status == 400 else "INTERNAL_SERVER_ERROR"
    )
    assert PRIVATE not in response.text
    assert rendered == []
    assert case.logger.error.call_count >= 1
    assert all(
        not call.kwargs.get("exc_info")
        for call in case.logger.error.call_args_list
    )
    assert_private_diagnostics(case, capsys)


@pytest.mark.parametrize("endpoint", ["numbers", "checked"])
def test_validation_keeps_field_location_without_input_or_error_context(
    boundary_app,
    capsys,
    endpoint,
):
    case = boundary_app
    response = case.client.post(
        f"/fixture/{endpoint}?token={PRIVATE}",
        json={"value": PRIVATE},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    errors = body["details"]["validation_errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["body", "value"]
    assert set(errors[0]) == {"type", "loc", "msg"}
    assert errors[0]["msg"]
    assert PRIVATE not in response.text
    assert_private_diagnostics(case, capsys)


def test_missing_field_remains_actionable(boundary_app, capsys):
    case = boundary_app
    response = case.client.post("/fixture/numbers", json={})
    assert response.status_code == 422
    error = response.json()["error"]["details"]["validation_errors"][0]
    assert error == {
        "type": "missing",
        "loc": ["body", "value"],
        "msg": "Field required",
    }
    assert_private_diagnostics(case, capsys)


@pytest.mark.parametrize("streaming", [False, True])
def test_private_failures_do_not_escape_to_server_error_logging(
    boundary_app,
    capsys,
    streaming,
):
    case = boundary_app

    @case.app.get("/fixture/server-failure")
    async def fail():
        if not streaming:
            raise RuntimeError(PRIVATE)

        async def body():
            yield b"first chunk"
            raise RuntimeError(PRIVATE)

        return StreamingResponse(body())

    with TestClient(case.app, raise_server_exceptions=True) as client:
        if streaming:
            with pytest.raises(RuntimeError) as failure:
                client.get("/fixture/server-failure")
            rendered = "".join(traceback.format_exception(failure.value))
            assert PRIVATE not in rendered
            assert str(failure.value) == "Response body failed"
        else:
            response = client.get("/fixture/server-failure")
            assert response.status_code == 500
            assert PRIVATE not in response.text
    assert_private_diagnostics(case, capsys)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_non_http_scope_preserves_original_failure(
    monkeypatch, scope_type
):
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "disabled", False)
    original = RuntimeError(PRIVATE)

    async def application(scope, receive, send):
        raise original

    handler = AsyncMock()
    middleware = HTTPErrorPrivacyMiddleware(application, handler)
    with pytest.raises(RuntimeError) as failure:
        await middleware({"type": scope_type}, AsyncMock(), AsyncMock())
    assert failure.value is original
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_an_http_failure(monkeypatch):
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "disabled", False)
    original = asyncio.CancelledError()

    async def application(scope, receive, send):
        raise original

    handler = AsyncMock()
    middleware = HTTPErrorPrivacyMiddleware(application, handler)
    with pytest.raises(asyncio.CancelledError) as failure:
        await middleware({"type": "http"}, AsyncMock(), AsyncMock())
    assert failure.value is original
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_started_response_is_not_replaced_or_reported_complete(
    monkeypatch,
):
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "disabled", False)

    async def application(scope, receive, send):
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send(
            {"type": "http.response.body", "body": b"part", "more_body": True}
        )
        raise RuntimeError(PRIVATE)

    handler = AsyncMock()
    send = AsyncMock()
    middleware = HTTPErrorPrivacyMiddleware(application, handler)
    with pytest.raises(RuntimeError, match="^Response body failed$"):
        await middleware({"type": "http", "method": "GET"}, AsyncMock(), send)
    handler.assert_not_awaited()
    assert [call.args[0]["type"] for call in send.await_args_list] == [
        "http.response.start",
        "http.response.body",
    ]
    assert send.await_args_list[-1].args[0]["more_body"] is True


def test_real_server_logs_exclude_request_and_failure_values(
    boundary_app, caplog
):
    case = boundary_app

    @case.app.get("/fixture/network/{item_id}")
    async def fail(item_id: str):
        raise RuntimeError(PRIVATE)

    @case.app.get("/fixture/network-stream")
    async def stream():
        async def body():
            yield b"first chunk"
            raise RuntimeError(PRIVATE)

        return StreamingResponse(body())

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            case.app,
            log_config=None,
            access_log=True,
            lifespan="off",
        )
    )
    worker = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    caplog.set_level(logging.INFO)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while (
            not server.started
            and worker.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert server.started
        connection.request(
            "GET", f"/fixture/network/{PRIVATE}?token={PRIVATE}"
        )
        response = connection.getresponse()
        assert response.status == 500
        assert PRIVATE.encode() not in response.read()
        connection.request("GET", f"/fixture/network-stream?token={PRIVATE}")
        response = connection.getresponse()
        assert response.status == 200
        with pytest.raises(http.client.IncompleteRead) as failure:
            response.read()
        assert failure.value.partial == b"first chunk"
    finally:
        connection.close()
        server.should_exit = True
        worker.join(timeout=10)
        listener.close()
    assert not worker.is_alive()
    assert PRIVATE not in caplog.text
    assert "Response body failed" in caplog.text
    assert not any(
        record.name == "uvicorn.access" for record in caplog.records
    )


@pytest.mark.parametrize(
    "enabled,billing,configured,provided,status",
    [
        (True, True, True, None, 403),
        (True, True, True, "wrong-key", 403),
        (True, True, True, "fixture-key", 200),
        (True, True, False, "fixture-key", 403),
        (False, True, False, None, 200),
        (True, False, False, None, 200),
        (False, False, False, None, 200),
    ],
)
def test_service_identity_preserves_token_gate(
    boundary_app,
    monkeypatch,
    enabled,
    billing,
    configured,
    provided,
    status,
):
    case = boundary_app
    monkeypatch.setattr(
        service_auth,
        "get_settings",
        lambda: SimpleNamespace(
            saas_enabled=enabled,
            saas_billing_enabled=billing,
            saas_internal_token=SecretStr("fixture-key")
            if configured
            else None,
        ),
    )

    @case.app.get("/v1/fixture/identity")
    async def endpoint():
        return {"ok": True}

    headers = {"X-SaaS-Service-Token": provided} if provided else {}
    response = case.client.get("/v1/fixture/identity", headers=headers)
    assert response.status_code == status
    assert response.json() == (
        {"ok": True} if status == 200 else {"detail": "内部服务身份无效"}
    )


def test_service_identity_keeps_public_paths_accessible(
    boundary_app, monkeypatch
):
    monkeypatch.setattr(
        service_auth,
        "get_settings",
        lambda: SimpleNamespace(
            saas_enabled=True,
            saas_billing_enabled=True,
            saas_internal_token=None,
        ),
    )
    response = boundary_app.client.get("/fixture/items/known")
    assert response.status_code == 200
    assert response.json() == {"item_id": "known", "ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_service_identity_keeps_non_http_delegation(
    monkeypatch, scope_type
):
    settings = Mock(side_effect=AssertionError("HTTP auth should not run"))
    monkeypatch.setattr(service_auth, "get_settings", settings)
    application = AsyncMock(return_value=None)
    middleware = service_auth.ServiceIdentityMiddleware(application)
    scope = {"type": scope_type}
    receive, send = AsyncMock(), AsyncMock()
    await middleware(scope, receive, send)
    application.assert_awaited_once_with(scope, receive, send)
    settings.assert_not_called()
