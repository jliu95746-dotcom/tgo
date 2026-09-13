"""Testing an unsaved selection must not silently execute the saved tool."""

from unittest.mock import AsyncMock
from uuid import uuid4

from app.api.v1.endpoints import customer_logistics
from app.services.logistics_result_parser import ParsedTrackingResult


def test_configuration_test_passes_the_selected_tool_without_saving(client, monkeypatch, authenticated_project):
    selected = uuid4()
    service = type("Service", (), {})()
    service.execute_live_query = AsyncMock(return_value=ParsedTrackingResult("in_transit", None, None, "运输中", ()))
    monkeypatch.setattr(customer_logistics, "_service", lambda _: service)
    response = client.post(
        "/v1/logistics/settings/test", json={"tracking_no": "SF1234567890", "query_tool_id": str(selected)}
    )
    assert response.status_code == 200
    service.execute_live_query.assert_awaited_once_with(
        authenticated_project.id, "SF1234567890", query_tool_id=selected
    )


def test_query_verification_fields_are_forwarded(client, monkeypatch, authenticated_project):
    service = type("Service", (), {})()
    service.execute_live_query = AsyncMock(return_value=ParsedTrackingResult("in_transit", None, None, "运输中", ()))
    monkeypatch.setattr(customer_logistics, "_service", lambda _: service)
    response = client.post("/v1/logistics/settings/test", json={
        "tracking_no": "SF1234567890", "carrier_code": "SF", "phone": "1234",
    })
    assert response.status_code == 200
    service.execute_live_query.assert_awaited_once_with(
        authenticated_project.id, "SF1234567890", query_tool_id=None,
        carrier_code="SF", phone="1234",
    )


def test_query_rejects_invalid_phone(client):
    response = client.post("/v1/logistics/settings/test", json={
        "tracking_no": "SF1234567890", "phone": "not-a-phone",
    })
    assert response.status_code == 422
