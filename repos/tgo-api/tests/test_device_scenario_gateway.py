"""Device scenario drafts use the logged-in project and preserve conflicts."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import device_control
from app.schemas.device_scenario import DeviceScenario, DeviceScenarioDraftWrite


def test_business_system_contract_matches_device_service():
    request = DeviceScenarioDraftWrite(
        expected_revision=0, purpose="整理资料", business_system=" 订单后台 "
    )
    assert request.model_dump()["business_system"] == "订单后台"
    assert DeviceScenarioDraftWrite(
        expected_revision=0, purpose="整理资料", business_system=" "
    ).business_system is None


@pytest.mark.asyncio
async def test_old_draft_writer_does_not_erase_business_system(monkeypatch):
    from datetime import datetime, timezone
    from app.services.device_control_client import device_control_client

    project_id, device_id = uuid4(), uuid4()
    request = DeviceScenarioDraftWrite(expected_revision=4, purpose="继续整理")
    send = AsyncMock(return_value={
        "project_id": str(project_id), "device_id": str(device_id),
        "scenario": "files", "purpose": "继续整理",
        "business_system": "订单后台", "target_directory": None,
        "revision": 5, "status": "draft",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    monkeypatch.setattr(device_control_client, "_request", send)
    await device_control_client.save_scenario_draft(
        device_id, DeviceScenario.FILES, str(project_id), request
    )
    assert "business_system" not in send.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_draft_save_uses_current_staff_project(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    staff = MagicMock(project_id=project_id)
    request = DeviceScenarioDraftWrite(expected_revision=0, purpose="整理资料")
    save = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        device_control.device_control_client, "save_scenario_draft", save
    )
    await device_control.save_device_scenario_draft(
        device_id, DeviceScenario.FILES, request, staff
    )
    save.assert_awaited_once_with(
        device_id, DeviceScenario.FILES, str(project_id), request
    )


@pytest.mark.asyncio
async def test_draft_revision_conflict_preserves_http_409(monkeypatch):
    request = httpx.Request("PUT", "http://device-control.test/v1/devices")
    response = httpx.Response(
        409, json={"detail": "Device scenario draft changed"}, request=request
    )
    save = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "conflict", request=request, response=response
        )
    )
    monkeypatch.setattr(
        device_control.device_control_client, "save_scenario_draft", save
    )
    with pytest.raises(HTTPException) as error:
        await device_control.save_device_scenario_draft(
            uuid4(),
            DeviceScenario.FILES,
            DeviceScenarioDraftWrite(expected_revision=1, purpose="整理资料"),
            MagicMock(project_id=uuid4()),
        )
    assert error.value.status_code == 409
