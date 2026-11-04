"""Recover an execution with metadata alone and never regain POST authority."""

from dataclasses import fields

import httpx
import pytest

from app.exceptions import ExternalServiceError
from app.schemas.device_script_execution import ScriptReceiptReference
from app.services import device_control_client as transport
from app.services import device_execution as lifecycle
from app.services.device_script_execution import ApprovedScriptExecution
from tests.test_device_script_execution import (  # noqa: F401
    setup,  # noqa: F401
    connect,
    receipt,
)  # noqa: F401


async def test_receipt_reference_needs_no_private_input(  # noqa: F811
    setup, monkeypatch  # noqa: F811
):
    context, item, bound = setup
    async with lifecycle.track_device_execution(context):
        call = ApprovedScriptExecution.prepare(bound, item, {"count": 2}).call
    reference = ScriptReceiptReference(
        **{
            field.name: getattr(call, field.name)
            for field in fields(ScriptReceiptReference)
        }
    )
    assert not hasattr(reference, "parameters_json")
    requests = []

    async def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.content == b""
        return httpx.Response(200, json=receipt(reference))

    connect(monkeypatch, handle)
    result = await transport.device_control_client.read_script(reference)
    assert result.fields == {"total": 3}
    with pytest.raises(ExternalServiceError):
        await transport.device_control_client.execute_script(reference)
    assert len(requests) == 1
