"""Store responses are untrusted; credentials never enter failure logs."""

from types import SimpleNamespace

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.services.store_client import StoreClient

PRIVATE = "synthetic-private-store-value"
OPERATIONS = ("get_model", "get_api_key", "install_model", "uninstall_model")
MODEL = {
    "id": "model-resource",
    "name": "model-name",
    "model_type": "chat",
    "input_price": 0.0,
    "output_price": 0.0,
    "provider": {"id": "provider-resource", "name": "Provider", "slug": "p"},
}
KEY = {
    "user_id": "store-user",
    "email": "user@example.invalid",
    "api_key": PRIVATE,
}


@pytest.fixture
def remote(monkeypatch):
    original = httpx.AsyncClient
    state = SimpleNamespace(
        response=httpx.Response(200, json={}),
        requests=[],
        options=[],
        error=None,
    )

    def handle(request):
        state.requests.append(request)
        if state.error is not None:
            raise state.error
        return state.response

    def factory(**kwargs):
        state.options.append(kwargs.copy())
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    state.client = StoreClient()
    state.client.base_url = "https://store.fixture.invalid/api/v1"
    return state


async def invoke(remote, operation):
    if operation == "get_api_key":
        return await remote.client.get_api_key(PRIVATE)
    return await getattr(remote.client, operation)("model-resource", PRIVATE)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "body",
    [b"", b"<html>bad</html>", b"null", b"[]", b"false", b"7", b'"text"'],
)
async def test_malformed_success_is_a_controlled_gateway_error(
    remote, operation, body
):
    remote.response = httpx.Response(200, content=body)
    with pytest.raises(ExternalServiceError) as failure:
        await invoke(remote, operation)
    assert failure.value.status_code == 502
    assert len(remote.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["user_id", "email", "api_key"])
@pytest.mark.parametrize("invalid", [None, 1, False, "", "  \t", [], {}])
async def test_credentials_require_complete_nonblank_strings(
    remote, field, invalid, caplog
):
    remote.response = httpx.Response(200, json={**KEY, field: invalid})
    with pytest.raises(ExternalServiceError) as failure:
        await remote.client.get_api_key(PRIVATE)
    assert failure.value.status_code == 502
    assert PRIVATE not in str(failure.value)
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["user_id", "email", "api_key"])
async def test_missing_credential_field_is_rejected(remote, field):
    body = KEY.copy()
    del body[field]
    remote.response = httpx.Response(200, json=body)
    with pytest.raises(ExternalServiceError):
        await remote.client.get_api_key(PRIVATE)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "provider"])
async def test_invalid_model_schema_is_not_exposed(remote, field, caplog):
    remote.response = httpx.Response(
        200, json={**MODEL, field: {"private": PRIVATE}}
    )
    with pytest.raises(ExternalServiceError) as failure:
        await remote.client.get_model("model-resource", PRIVATE)
    assert PRIVATE not in str(failure.value)
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_http_error_keeps_status_but_does_not_log_body(
    remote, operation, caplog
):
    remote.response = httpx.Response(403, text=PRIVATE)
    with pytest.raises(httpx.HTTPStatusError) as failure:
        await invoke(remote, operation)
    assert failure.value.response.status_code == 403
    assert any(
        getattr(record, "status_code", None) == 403
        for record in caplog.records
    )
    assert PRIVATE not in caplog.text
    assert len(remote.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, RuntimeError]
)
async def test_connection_failures_never_log_exception_text(
    remote, operation, error, caplog
):
    remote.error = error(PRIVATE)
    with pytest.raises(error):
        await invoke(remote, operation)
    assert PRIVATE not in caplog.text
    assert any(
        getattr(record, "error_type", None) == error.__name__
        for record in caplog.records
    )
    assert len(remote.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "id",
        "name",
        "model_type",
        "provider.id",
        "provider.name",
        "provider.slug",
    ],
)
@pytest.mark.parametrize("value", ["", " \t "])
async def test_blank_model_identifiers_cannot_be_installed(
    remote, field, value
):
    body = {**MODEL, "provider": MODEL["provider"].copy()}
    if field.startswith("provider."):
        body["provider"][field.split(".")[1]] = value
    else:
        body[field] = value
    remote.response = httpx.Response(200, json=body)
    with pytest.raises(ExternalServiceError):
        await remote.client.get_model("model-resource", PRIVATE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,method,path,body",
    [
        ("get_model", "GET", "/models/model-resource", MODEL),
        ("get_api_key", "GET", "/auth/api-key", {**KEY, "future": "ignored"}),
        ("install_model", "POST", "/install/model/model-resource", {}),
        (
            "uninstall_model",
            "DELETE",
            "/install/model/model-resource",
            {"success": True},
        ),
    ],
)
async def test_valid_requests_keep_url_headers_timeout_and_body(
    remote, operation, method, path, body
):
    remote.response = httpx.Response(200, json=body)
    result = await invoke(remote, operation)
    if operation == "get_model":
        assert result.name == MODEL["name"]
        assert result.provider.id == MODEL["provider"]["id"]
    elif operation == "get_api_key":
        assert {key: result[key] for key in KEY} == KEY
    else:
        assert result == body
    request = remote.requests[0]
    assert request.method == method
    assert str(request.url) == remote.client.base_url + path
    assert request.content == b""
    if operation == "get_api_key":
        assert request.headers["authorization"] == "Bearer " + PRIVATE
        assert "x-api-key" not in request.headers
    else:
        assert request.headers["x-api-key"] == PRIVATE
        assert "authorization" not in request.headers
    assert remote.options[0]["timeout"] == remote.client.timeout
    assert "follow_redirects" not in remote.options[0]
    assert "trust_env" not in remote.options[0]
