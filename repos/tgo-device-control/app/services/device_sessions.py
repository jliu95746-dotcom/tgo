"""Tenant-scoped device runs with leases and race-safe step transitions."""

from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, DeviceSession, DeviceSessionStep
from app.schemas.device_session import (
    FinishStatus,
    SessionDetail,
    SessionList,
    SessionStart,
    SessionStatus,
    SessionStep,
    SessionSummary,
    StepStatus,
)

LEASE_SECONDS = 60


def effective_session_status(
    session: DeviceSession, now: datetime
) -> SessionStatus:
    if session.status == "running" and (
        session.lease_expires_at is None or session.lease_expires_at <= now
    ):
        return "interrupted"
    return cast(SessionStatus, session.status)


class DeviceSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _owned(
        self,
        project: UUID,
        session: UUID,
        device: UUID | None = None,
        *,
        lock: bool = False,
    ) -> tuple[DeviceSession, str]:
        query = (
            select(DeviceSession, Device.device_name)
            .join(Device)
            .where(Device.project_id == project, DeviceSession.id == session)
            .execution_options(populate_existing=True)
        )
        if device is not None:
            query = query.where(Device.id == device)
        if lock:
            query = query.with_for_update(of=DeviceSession)
        row = (await self.db.execute(query)).one_or_none()
        if row is None:
            raise HTTPException(404, "Device session not found")
        return row[0], row[1]

    @staticmethod
    def _require_active(session: DeviceSession) -> None:
        if (
            effective_session_status(session, datetime.now(timezone.utc))
            != "running"
        ):
            raise HTTPException(409, "Device session is no longer active")

    @staticmethod
    def _summary(session: DeviceSession, device_name: str) -> SessionSummary:
        status = effective_session_status(session, datetime.now(timezone.utc))
        return SessionSummary(
            id=session.id,
            device_id=session.device_id,
            device_name=device_name,
            agent_id=session.agent_id,
            agent_name=session.agent_name,
            status=status,
            started_at=session.started_at,
            ended_at=session.lease_expires_at
            if status == "interrupted" and session.ended_at is None
            else session.ended_at,
            lease_expires_at=session.lease_expires_at,
            actions_count=session.actions_count,
            failed_actions_count=session.failed_actions_count,
            screenshots_count=session.screenshots_count,
        )

    async def start(
        self,
        project: UUID,
        device: UUID,
        session: UUID,
        request: SessionStart,
    ) -> SessionSummary:
        # Lock the owned device to serialize duplicate starts and deletion.
        device_row = await self.db.scalar(
            select(Device)
            .where(Device.id == device, Device.project_id == project)
            .with_for_update()
        )
        if device_row is None:
            raise HTTPException(404, "Device not found")
        existing = await self.db.get(
            DeviceSession, session, populate_existing=True
        )
        if existing is not None:
            if (
                existing.device_id != device
                or existing.agent_id != request.agent_id
            ):
                raise HTTPException(409, "Device session identity conflict")
            summary = self._summary(existing, device_row.device_name)
            await self.db.commit()
            return summary
        now = datetime.now(timezone.utc)
        row = DeviceSession(
            id=session,
            device_id=device,
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            status="running",
            started_at=now,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            actions_count=0,
            screenshots_count=0,
            failed_actions_count=0,
        )
        self.db.add(row)
        await self.db.flush()
        summary = self._summary(row, device_row.device_name)
        await self.db.commit()
        return summary

    async def heartbeat(
        self, project: UUID, device: UUID, session: UUID
    ) -> SessionSummary:
        row, name = await self._owned(project, session, device, lock=True)
        self._require_active(row)
        row.lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=LEASE_SECONDS
        )
        summary = self._summary(row, name)
        await self.db.commit()
        return summary

    async def finish(
        self,
        project: UUID,
        device: UUID,
        session: UUID,
        status: FinishStatus,
    ) -> SessionSummary:
        row, name = await self._owned(project, session, device, lock=True)
        if row.status != "running":
            summary = self._summary(row, name)
            await self.db.commit()
            # Late delivery must not overwrite a terminal run.
            return summary
        self._require_active(row)
        pending = await self.db.scalar(
            select(func.count())
            .select_from(DeviceSessionStep)
            .where(
                DeviceSessionStep.session_id == session,
                DeviceSessionStep.status == "running",
            )
        )
        if status == "completed" and pending:
            raise HTTPException(409, "Device operations are still running")
        now = datetime.now(timezone.utc)
        if pending:
            await self.db.execute(
                update(DeviceSessionStep)
                .where(
                    DeviceSessionStep.session_id == session,
                    DeviceSessionStep.status == "running",
                )
                .values(status="interrupted", ended_at=now)
            )
            row.failed_actions_count += pending
        row.status = status
        row.ended_at = now
        summary = self._summary(row, name)
        await self.db.commit()
        return summary

    async def begin_step(
        self,
        project: UUID,
        device: UUID,
        session: UUID,
        tool_name: str,
    ) -> UUID:
        row, _ = await self._owned(project, session, device, lock=True)
        self._require_active(row)
        if not tool_name or len(tool_name) > 255:
            raise HTTPException(422, "Invalid tool name")
        step_id = uuid4()
        self.db.add(
            DeviceSessionStep(
                id=step_id,
                session_id=session,
                tool_name=tool_name,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
        )
        row.actions_count += 1
        await self.db.commit()
        return step_id

    async def end_step(
        self,
        project: UUID,
        device: UUID,
        session: UUID,
        step: UUID,
        status: StepStatus,
        *,
        screenshots: int = 0,
    ) -> None:
        row, _ = await self._owned(project, session, device, lock=True)
        record = await self.db.scalar(
            select(DeviceSessionStep)
            .where(
                DeviceSessionStep.id == step,
                DeviceSessionStep.session_id == session,
            )
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise HTTPException(404, "Device operation not found")
        if (
            record.status != "running"
            or effective_session_status(row, datetime.now(timezone.utc))
            != "running"
        ):
            await self.db.commit()
            return
        if status == "running" or screenshots < 0:
            raise HTTPException(422, "Invalid operation completion")
        record.status = status
        record.ended_at = datetime.now(timezone.utc)
        row.screenshots_count += screenshots
        if status != "completed":
            row.failed_actions_count += 1
        await self.db.commit()

    async def get(
        self,
        project: UUID,
        session: UUID,
        *,
        step_skip: int = 0,
        step_limit: int = 100,
    ) -> SessionDetail:
        row, name = await self._owned(project, session)
        summary = self._summary(row, name)
        records = (
            await self.db.scalars(
                select(DeviceSessionStep)
                .where(DeviceSessionStep.session_id == session)
                .order_by(
                    DeviceSessionStep.started_at.desc(),
                    DeviceSessionStep.id.desc(),
                )
                .offset(step_skip)
                .limit(step_limit)
            )
        ).all()
        steps = [SessionStep.model_validate(record) for record in records]
        if summary.status != "running":
            steps = [
                step.model_copy(
                    update={
                        "status": "interrupted",
                        "ended_at": summary.ended_at,
                    }
                )
                if step.status == "running"
                else step
                for step in steps
            ]
        count = await self.db.scalar(
            select(func.count()).select_from(DeviceSessionStep).where(
                DeviceSessionStep.session_id == session
            )
        )
        return SessionDetail(
            **summary.model_dump(), steps=steps, step_total=count or 0
        )

    async def list(
        self,
        project: UUID,
        *,
        device_id: UUID | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> SessionList:
        query = (
            select(DeviceSession, Device.device_name)
            .join(Device)
            .where(Device.project_id == project)
        )
        if device_id is not None:
            query = query.where(Device.id == device_id)
        count = await self.db.scalar(
            select(func.count()).select_from(query.subquery())
        )
        rows = (
            await self.db.execute(
                query.order_by(
                    DeviceSession.started_at.desc(), DeviceSession.id.desc()
                )
                .offset(skip)
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        ).all()
        return SessionList(
            sessions=[self._summary(row[0], row[1]) for row in rows],
            total=count or 0,
        )
