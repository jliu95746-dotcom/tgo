"""Device actions need valid receipts; upstream errors remain private."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app.api.v1.endpoints import device_control as gateway
from app.core.exceptions import ExternalServiceError
from app.core.security import get_current_active_user
from app.services.device_control_client import DeviceControlClient

PRIVATE = "synthetic-private-device-response"


@pytest.fixture
def remote(monkeypatch):
    original = httpx.AsyncClient
    state = SimpleNamespace(response=httpx.Response(200, json={}), requests=[])

    def handle(request):
        state.requests.append(request)
        return state.response

    def client_factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handle))
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    state.client = DeviceControlClient()
    state.client.base_url = "http://device.fixture.invalid"
    state.project, state.device = str(uuid4()), str(uuid4())
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [b"", b"<html>bad</html>", b"null", b"false", b"[]", b"7", b'"text"'],
)
async def test_invalid_success_response_is_not_an_object(remote, body):
    remote.response = httpx.Response(200, content=body)
    with pytest.raises(ExternalServiceError) as failure:
        await remote.client.list_devices(remote.project)
    assert failure.value.status_code == 502
    assert len(remote.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{}, {"devices": [], "total": 0}, {"success": False}]
)
async def test_valid_json_objects_remain_unchanged(remote, body):
    remote.response = httpx.Response(200, json=body)
    assert (
        await remote.client._request("GET", "/synthetic", remote.project)
        == body
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete_device", "disconnect_device"])
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"success": False},
        {"success": 0},
        {"success": 1},
        {"success": "true"},
        {"success": None},
    ],
)
async def test_action_does_not_succeed_without_explicit_confirmation(
    remote, operation, body
):
    remote.response = httpx.Response(200, json=body)
    with pytest.raises(ExternalServiceError) as failure:
        await getattr(remote.client, operation)(remote.device, remote.project)
    assert failure.value.status_code == 502
    assert len(remote.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete_device", "disconnect_device"])
async def test_confirmed_action_still_succeeds_once(remote, operation):
    remote.response = httpx.Response(200, json={"success": True})
    assert (
        await getattr(remote.client, operation)(remote.device, remote.project)
        is True
    )
    assert len(remote.requests) == 1


@pytest.mark.parametrize(
    "status", [400, 401, 403, 404, 409, 422, 429, 500, 503]
)
@pytest.mark.parametrize("body", [{"detail": PRIVATE}, PRIVATE])
def test_error_mapping_retains_status_without_upstream_body(
    status, body, caplog
):
    request = httpx.Request("GET", "http://device.fixture.invalid")
    response = httpx.Response(status, request=request, json=body)
    error = httpx.HTTPStatusError(PRIVATE, request=request, response=response)
    with pytest.raises(HTTPException) as failure:
        gateway._handle_service_error(error, "fixture")
    assert failure.value.status_code == (status if status < 500 else 502)
    assert PRIVATE not in str(failure.value.detail)
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ConnectError(PRIVATE), 502),
        (httpx.ReadTimeout(PRIVATE), 502),
        (RuntimeError(PRIVATE), 500),
    ],
)
def test_network_and_unexpected_errors_do_not_expose_exception_text(
    error, expected, caplog
):
    with pytest.raises(HTTPException) as failure:
        gateway._handle_service_error(error, "fixture")
    assert failure.value.status_code == expected
    assert PRIVATE not in str(failure.value.detail)
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        HTTPException(409, "配置已变化"),
        ExternalServiceError("device-control", "设备服务暂不可用"),
    ],
)
def test_existing_public_domain_errors_are_preserved(error):
    with pytest.raises(type(error)) as failure:
        gateway._handle_service_error(error, "fixture")
    assert failure.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 409, 422, 503])
async def test_gateway_response_does_not_serialize_upstream_private_text(
    remote, monkeypatch, status
):
    remote.response = httpx.Response(status, json={"detail": PRIVATE})
    app = FastAPI()
    app.include_router(gateway.router)
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(
        project_id=uuid4(),
        role="admin",
    )
    monkeypatch.setattr(gateway, "device_control_client", remote.client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/devices")
    assert response.status_code == (status if status < 500 else 502)
    assert PRIVATE not in response.text
    assert len(remote.requests) == 1
