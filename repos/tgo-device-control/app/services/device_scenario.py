"""Save device use-case drafts without changing the device's execution rights."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_scenario import DeviceScenarioDraft
from app.schemas.device_scenario import (
    DeviceScenario,
    DeviceScenarioDraftRead,
    DeviceScenarioDraftWrite,
)


class DeviceScenarioService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _require_device(self, project_id: UUID, device_id: UUID) -> None:
        device = await self.db.scalar(
            select(Device.id).where(
                Device.id == device_id, Device.project_id == project_id
            )
        )
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

    async def get(
        self, project_id: UUID, device_id: UUID, scenario: DeviceScenario
    ) -> DeviceScenarioDraftRead | None:
        await self._require_device(project_id, device_id)
        row = await self.db.scalar(
            select(DeviceScenarioDraft).where(
                DeviceScenarioDraft.project_id == project_id,
                DeviceScenarioDraft.device_id == device_id,
                DeviceScenarioDraft.scenario == scenario.value,
            )
        )
        return DeviceScenarioDraftRead.model_validate(row) if row else None

    async def save(
        self,
        project_id: UUID,
        device_id: UUID,
        scenario: DeviceScenario,
        request: DeviceScenarioDraftWrite,
    ) -> DeviceScenarioDraftRead:
        await self._require_device(project_id, device_id)
        row = await self.db.scalar(
            select(DeviceScenarioDraft)
            .where(
                DeviceScenarioDraft.project_id == project_id,
                DeviceScenarioDraft.device_id == device_id,
                DeviceScenarioDraft.scenario == scenario.value,
            )
            .with_for_update()
        )
        if row is None:
            if request.expected_revision != 0:
                raise HTTPException(
                    status_code=409, detail="Device scenario draft changed"
                )
            row = DeviceScenarioDraft(
                device_id=device_id,
                project_id=project_id,
                scenario=scenario.value,
                purpose=request.purpose.strip(),
                business_system=request.business_system,
                target_directory=request.target_directory,
                revision=1,
            )
            self.db.add(row)
        else:
            if row.revision != request.expected_revision:
                raise HTTPException(
                    status_code=409, detail="Device scenario draft changed"
                )
            row.purpose = request.purpose.strip()
            if "business_system" in request.model_fields_set:
                row.business_system = request.business_system
            row.target_directory = request.target_directory
            row.revision += 1
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="Device scenario draft changed"
            ) from None
        await self.db.refresh(row)
        return DeviceScenarioDraftRead.model_validate(row)
