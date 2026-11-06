"""Plugin failures keep their status contract without private diagnostics."""

import logging

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.plugins import _handle_runtime_error
from app.core.exceptions import TGOAPIException
from app.services.plugin_runtime_client import PluginRuntimeClient

MARKER = "synthetic-private-plugin-error"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
def test_plain_client_error_never_echoes_body_or_url(caplog, status):
    request = httpx.Request("GET", "http://plugins.internal/" + MARKER)
    response = httpx.Response(status, text=MARKER, request=request)
    error = httpx.HTTPStatusError(MARKER, request=request, response=response)
    with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as raised:
        _handle_runtime_error(error, "get_plugin")
    assert raised.value.status_code == status
    assert MARKER not in str(raised.value.detail)
    records = [r for r in caplog.records if r.name == "api.plugins"]
    assert all(MARKER not in str(r.__dict__) for r in records)


@pytest.mark.parametrize("status", [400, 403, 409, 422])
def test_structured_client_error_preserves_detail(status):
    request = httpx.Request("GET", "http://plugins.internal/synthetic")
    detail = {"message": "controlled", "errors": []}
    response = httpx.Response(status, json={"detail": detail}, request=request)
    error = httpx.HTTPStatusError(
        "controlled", request=request, response=response
    )
    with pytest.raises(HTTPException) as raised:
        _handle_runtime_error(error, "get_plugin")
    assert raised.value.status_code == status and raised.value.detail == detail


@pytest.mark.parametrize("value", [{"error": MARKER}, [MARKER], MARKER])
def test_unknown_json_error_does_not_echo_entire_body(value):
    request = httpx.Request("GET", "http://plugins.internal/synthetic")
    response = httpx.Response(400, json=value, request=request)
    error = httpx.HTTPStatusError(
        "controlled", request=request, response=response
    )
    with pytest.raises(HTTPException) as raised:
        _handle_runtime_error(error, "get_plugin")
    assert raised.value.status_code == 400
    assert MARKER not in str(raised.value.detail)


@pytest.mark.parametrize(
    "error,status",
    [
        (RuntimeError(MARKER), 500),
        (httpx.ConnectError(MARKER), 502),
    ],
)
def test_unexpected_and_transport_errors_keep_private_values_out(
    caplog, error, status
):
    with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as raised:
        _handle_runtime_error(error, "list_plugins")
    assert raised.value.status_code == status
    assert MARKER not in str(raised.value.detail)
    records = [r for r in caplog.records if r.name == "api.plugins"]
    assert records and all(MARKER not in str(r.__dict__) for r in records)


@pytest.mark.parametrize(
    "original",
    [
        HTTPException(409, "controlled"),
        TGOAPIException("controlled", status_code=403),
    ],
)
def test_existing_public_exceptions_are_preserved(original):
    with pytest.raises(type(original)) as error:
        _handle_runtime_error(original, "get_plugin")
    assert error.value is original


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 503])
async def test_sdk_and_endpoint_logs_do_not_include_upstream_body(
    monkeypatch,
    caplog,
    status,
):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text=MARKER),
            ),
        ),
    )
    service = PluginRuntimeClient()
    service.base_url = "http://plugins.internal/" + MARKER
    with caplog.at_level(logging.INFO):
        with pytest.raises(httpx.HTTPStatusError) as upstream:
            await service._request(
                "GET", "/synthetic", params={"private": MARKER}
            )
        with pytest.raises(HTTPException) as error:
            _handle_runtime_error(upstream.value, "get_plugin")
    assert error.value.status_code == (status if status < 500 else 502)
    assert MARKER not in str(error.value.detail)
    records = [
        r
        for r in caplog.records
        if r.name
        in {
            "services.plugin_runtime_client",
            "api.plugins",
        }
    ]
    assert records and all(MARKER not in str(r.__dict__) for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, [], MARKER, 7])
async def test_sdk_rejects_success_with_wrong_object_shape(monkeypatch, value):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=value),
            ),
        ),
    )
    service = PluginRuntimeClient()
    service.base_url = "http://plugins.internal"
    with pytest.raises(HTTPException) as error:
        await service._request("GET", "/synthetic")
    assert error.value.status_code == 502
    assert MARKER not in str(error.value.detail)


@pytest.mark.asyncio
async def test_sdk_preserves_normal_json_request_and_response(monkeypatch):
    import json

    original = httpx.AsyncClient
    requests, client_options = [], []
    expected = {
        "zero": 0,
        "false": False,
        "empty": "",
        "extra": {"unknown": None},
    }

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=expected)

    def client(**options):
        client_options.append(options)
        return original(**options, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    service = PluginRuntimeClient()
    service.base_url = "http://plugins.internal"
    service.timeout = 9
    result = await service._request(
        "POST",
        "/synthetic",
        json=expected,
        params={"project_id": "owned", "offset": 0},
    )
    assert result == expected and client_options == [{"timeout": 9}]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and request.url.path == "/synthetic"
    assert request.url.params.multi_items() == [
        ("project_id", "owned"),
        ("offset", "0"),
    ]
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == expected


@pytest.mark.asyncio
async def test_sdk_keeps_not_found_and_empty_catalog_defaults(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(404, text=MARKER),
            ),
        ),
    )
    service = PluginRuntimeClient()
    service.base_url = "http://plugins.internal"
    assert await service.get_plugin("missing", "owned") is None
    assert await service.list_plugins("owned") == {"plugins": [], "total": 0}
    assert await service.get_chat_toolbar_buttons("owned") == {"buttons": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
async def test_sdk_transport_error_is_not_retried_or_logged_raw(
    monkeypatch,
    caplog,
    exception,
):
    original = httpx.AsyncClient
    attempts = []

    def fail(request):
        attempts.append(request)
        raise exception(MARKER, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(fail),
        ),
    )
    service = PluginRuntimeClient()
    service.base_url = "http://plugins.internal/" + MARKER
    with caplog.at_level(logging.INFO):
        with pytest.raises(httpx.RequestError) as upstream:
            await service._request(
                "POST", "/synthetic", json={"private": MARKER}
            )
        with pytest.raises(HTTPException) as error:
            _handle_runtime_error(upstream.value, "create_plugin")
    assert error.value.status_code == 502 and len(attempts) == 1
    records = [
        r
        for r in caplog.records
        if r.name
        in {
            "services.plugin_runtime_client",
            "api.plugins",
        }
    ]
    assert records and all(MARKER not in str(r.__dict__) for r in records)
