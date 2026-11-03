"""Drafts are tenant-owned and cannot be mistaken for executable policy."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.device_scenario import DeviceScenarioDraft
from app.schemas.device_scenario import DeviceScenario, DeviceScenarioDraftWrite
from app.services.device_scenario import DeviceScenarioService


def test_draft_rejects_blank_purpose_and_negative_revision():
    with pytest.raises(ValidationError):
        DeviceScenarioDraftWrite(expected_revision=0, purpose="  ")
    with pytest.raises(ValidationError):
        DeviceScenarioDraftWrite(expected_revision=-1, purpose="整理资料")


def test_business_system_is_optional_but_normalized():
    request = DeviceScenarioDraftWrite(
        expected_revision=0, purpose="整理资料", business_system=" 订单后台 "
    )
    assert request.business_system == "订单后台"
    assert DeviceScenarioDraftWrite(
        expected_revision=0, purpose="整理资料", business_system="   "
    ).business_system is None
    with pytest.raises(ValidationError):
        DeviceScenarioDraftWrite(
            expected_revision=0, purpose="整理资料", business_system="订单\n后台"
        )


@pytest.mark.asyncio
async def test_other_project_cannot_read_or_save_device_draft():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    service = DeviceScenarioService(db)
    for operation in (
        service.get(uuid4(), uuid4(), DeviceScenario.FILES),
        service.save(
            uuid4(),
            uuid4(),
            DeviceScenario.FILES,
            DeviceScenarioDraftWrite(expected_revision=0, purpose="整理资料"),
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_revision_cannot_overwrite_draft():
    project_id, device_id = uuid4(), uuid4()
    row = DeviceScenarioDraft(
        project_id=project_id,
        device_id=device_id,
        scenario="files",
        purpose="原用途",
        revision=3,
        updated_at=datetime.now(timezone.utc),
    )
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[device_id, row])
    db.commit = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await DeviceScenarioService(db).save(
            project_id,
            device_id,
            DeviceScenario.FILES,
            DeviceScenarioDraftWrite(expected_revision=2, purpose="覆盖用途"),
        )
    assert error.value.status_code == 409
    assert row.purpose == "原用途"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_draft_is_marked_draft_and_increments_on_update():
    project_id, device_id = uuid4(), uuid4()
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[device_id, None])
    db.commit = AsyncMock()

    async def refresh(row):
        row.updated_at = datetime.now(timezone.utc)

    db.refresh = AsyncMock(side_effect=refresh)
    service = DeviceScenarioService(db)
    created = await service.save(
        project_id,
        device_id,
        DeviceScenario.FILES,
        DeviceScenarioDraftWrite(
            expected_revision=0, purpose=" 文件整理 ", business_system=" 订单后台 ",
            target_directory="C:\\Work"
        ),
    )
    assert created.status == "draft"
    assert created.revision == 1
    assert created.purpose == "文件整理"
    assert created.business_system == "订单后台"
    assert created.target_directory == "C:\\Work"
    row = db.add.call_args.args[0]
    db.scalar = AsyncMock(side_effect=[device_id, row])
    updated = await service.save(
        project_id,
        device_id,
        DeviceScenario.FILES,
        DeviceScenarioDraftWrite(
            expected_revision=1, purpose="核对资料", business_system="售后系统"
        ),
    )
    assert updated.revision == 2
    assert updated.purpose == "核对资料"
    assert updated.business_system == "售后系统"

    db.scalar = AsyncMock(side_effect=[device_id, row])
    unchanged = await service.save(
        project_id, device_id, DeviceScenario.FILES,
        DeviceScenarioDraftWrite(expected_revision=2, purpose="继续核对"),
    )
    assert unchanged.business_system == "售后系统"

    db.scalar = AsyncMock(side_effect=[device_id, row])
    cleared = await service.save(
        project_id, device_id, DeviceScenario.FILES,
        DeviceScenarioDraftWrite(
            expected_revision=3, purpose="结束核对", business_system=None
        ),
    )
    assert cleared.business_system is None
