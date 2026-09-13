"""Fail-closed device ownership checks at the service boundary."""

from uuid import uuid4

import httpx
import pytest

from app.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.services import device_control_client as module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["valid", "foreign", "forbidden", "mismatch", "invalid_json", "invalid_schema", "redirect", "timeout"]
)
async def test_binding_validation_http_boundary(monkeypatch, case):
    project, device = uuid4(), uuid4()
    requests = []
    original_client = httpx.AsyncClient

    async def handler(request):
        requests.append(request)
        assert request.url.path == f"/v1/devices/{device}"
        assert request.headers["Authorization"].startswith("Bearer ")
        if case == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if case in ("foreign", "forbidden"):
            return httpx.Response(404 if case == "foreign" else 403)
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        if case == "invalid_json":
            return httpx.Response(200, text="not-json")
        if case == "invalid_schema":
            return httpx.Response(200, json={"id": str(device)})
        return httpx.Response(
            200, json={"id": str(device), "project_id": str(uuid4() if case == "mismatch" else project)}
        )

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    if case == "valid":
        await module.device_control_client.validate_binding(project, str(device))
    else:
        error = NotFoundError if case in ("foreign", "forbidden", "mismatch") else ExternalServiceError
        with pytest.raises(error):
            await module.device_control_client.validate_binding(project, str(device))
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_invalid_device_identifier_never_opens_connection(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Invalid device identifier must be rejected before network access")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected)
    with pytest.raises(ValidationError):
        await module.device_control_client.validate_binding(uuid4(), "../foreign")
