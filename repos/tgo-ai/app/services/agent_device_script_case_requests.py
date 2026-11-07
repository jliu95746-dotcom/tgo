"""Durable preparation before I/O, with atomic save and cancel fencing."""

import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.exceptions import ConflictError, NotFoundError
from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.models.agent_device_script_case_request import ScriptCaseSaveRequest
from app.schemas.agent_device_script_cases import (
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
    ScriptCaseWrite,
)
from app.services.agent_activation import lock_account_agents

if TYPE_CHECKING:
    from app.services.agent_device_script_cases import (
        AgentDeviceScriptCaseService,
    )


class ScriptCaseRequests:
    def __init__(
        self,
        cases: "AgentDeviceScriptCaseService",
        *,
        preparation_seconds: float = 60,
    ) -> None:
        self.cases, self.db = cases, cases.db
        self.preparation_seconds = preparation_seconds

    async def _find(
        self, project: UUID, actor: UUID, request: UUID
    ) -> ScriptCaseSaveRequest | None:
        rows = await self.db.scalars(
            select(ScriptCaseSaveRequest)
            .where(
                ScriptCaseSaveRequest.project_id == project,
                ScriptCaseSaveRequest.actor_id == actor,
                ScriptCaseSaveRequest.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        return rows.first()

    @staticmethod
    def _scope(
        row: ScriptCaseSaveRequest, agent: UUID, control: ScriptCaseSaveControl
    ) -> None:
        if (
            row.agent_id != agent
            or row.binding_id != control.binding_id
            or row.binding_revision != control.binding_revision
            or row.case_id != control.case_id
            or row.expected_revision != control.expected_revision
        ):
            raise ConflictError("用例保存原请求范围不一致")

    @staticmethod
    def _new(
        project: UUID, actor: UUID, agent: UUID, control: ScriptCaseSaveControl
    ) -> ScriptCaseSaveRequest:
        return ScriptCaseSaveRequest(
            id=uuid4(),
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            request_id=control.request_id,
            binding_id=control.binding_id,
            binding_revision=control.binding_revision,
            case_id=control.case_id,
            expected_revision=control.expected_revision,
            created_at=datetime.now(timezone.utc),
        )

    async def _legacy(
        self, project: UUID, actor: UUID, agent: UUID, request: UUID
    ) -> ScriptCaseSaveRequest | None:
        saved = await self.db.scalar(
            select(AgentDeviceScriptCase).where(
                AgentDeviceScriptCase.project_id == project,
                AgentDeviceScriptCase.created_by == actor,
                AgentDeviceScriptCase.agent_id == agent,
                AgentDeviceScriptCase.request_id == request,
            )
        )
        if saved is None:
            return None
        row = self._new(
            project,
            actor,
            agent,
            ScriptCaseSaveControl(
                request_id=request,
                binding_id=saved.binding_id,
                binding_revision=saved.binding_revision,
                case_id=saved.case_id if saved.revision > 1 else None,
                expected_revision=saved.revision - 1,
            ),
        )
        row.created_at = saved.created_at
        row.deadline_at = row.completed_at = saved.created_at
        row.status, row.version_id = "saved", saved.id
        row.command_digest = saved.command_digest
        self.db.add(row)
        return row

    async def register(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptCaseWrite,
        digest: str,
    ) -> tuple[ScriptCaseSaveRequest, bool]:
        control = ScriptCaseSaveControl.model_validate(
            command.model_dump(include=set(ScriptCaseSaveControl.model_fields))
        )
        try:
            async with asyncio.timeout(10):
                await self.cases._authorize(project, actor, agent)
                await lock_account_agents(self.db, project)
                row = await self._find(project, actor, command.request_id)
                if row is None:
                    row = await self._legacy(
                        project, actor, agent, command.request_id
                    )
                if row is not None:
                    self._scope(row, agent, control)
                    if row.command_digest is not None and not (
                        hmac.compare_digest(row.command_digest, digest)
                    ):
                        raise ConflictError("用例保存原编号已用于其他内容")
                    await self.db.commit()
                    return row, False
                row = self._new(project, actor, agent, control)
                row.command_digest, row.status = digest, "preparing"
                row.deadline_at = row.created_at + timedelta(
                    seconds=self.preparation_seconds
                )
                self.db.add(row)
                await self.db.commit()
                return row, True
        except BaseException:
            await self.db.rollback()
            raise

    async def attach(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptCaseWrite,
        version: UUID,
    ) -> None:
        # Caller holds the project lock; commit this with the case version.
        row = await self._find(project, actor, command.request_id)
        control = ScriptCaseSaveControl.model_validate(
            command.model_dump(include=set(ScriptCaseSaveControl.model_fields))
        )
        if row is None:
            raise ConflictError("用例保存准备记录缺失")
        self._scope(row, agent, control)
        if row.status != "preparing" or row.deadline_at <= datetime.now(
            timezone.utc
        ):
            raise ConflictError("用例保存已取消、到期或结束，请查询原请求")
        row.status, row.version_id = "saved", version
        row.completed_at = datetime.now(timezone.utc)

    async def fail(self, project: UUID, actor: UUID, request: UUID) -> None:
        try:
            async with asyncio.timeout(5):
                await self.db.execute(
                    update(ScriptCaseSaveRequest)
                    .where(
                        ScriptCaseSaveRequest.project_id == project,
                        ScriptCaseSaveRequest.actor_id == actor,
                        ScriptCaseSaveRequest.request_id == request,
                        ScriptCaseSaveRequest.status == "preparing",
                    )
                    .values(
                        status="failed",
                        error_code="preparation_failed",
                        completed_at=datetime.now(timezone.utc),
                    )
                    .execution_options(synchronize_session=False)
                )
                await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise

    async def _view(self, row: ScriptCaseSaveRequest) -> ScriptCaseSaveState:
        saved = None
        if row.status == "saved" and row.version_id is not None:
            version = await self.cases._version(
                row.project_id, row.agent_id, row.version_id
            )
            if (
                version.created_by != row.actor_id
                or version.request_id != row.request_id
            ):
                raise ConflictError("用例保存回执范围不一致")
            saved = await self.cases._record(version)
        return ScriptCaseSaveState.model_validate(
            {
                **{
                    name: getattr(row, name)
                    for name in (
                        "project_id",
                        "actor_id",
                        "agent_id",
                        "request_id",
                        "binding_id",
                        "binding_revision",
                        "case_id",
                        "expected_revision",
                        "status",
                        "created_at",
                        "deadline_at",
                        "completed_at",
                        "error_code",
                    )
                },
                "case": saved,
            }
        )

    async def state(
        self, project: UUID, actor: UUID, agent: UUID, request: UUID
    ) -> ScriptCaseSaveState:
        return await self._observe(project, actor, agent, request)

    async def cancel(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        control: ScriptCaseSaveControl,
    ) -> ScriptCaseSaveState:
        return await self._observe(
            project, actor, agent, control.request_id, control
        )

    async def _observe(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
        cancel: ScriptCaseSaveControl | None = None,
    ) -> ScriptCaseSaveState:
        try:
            async with asyncio.timeout(10):
                await self.cases._authorize(project, actor, agent)
                await lock_account_agents(self.db, project)
                row = await self._find(project, actor, request)
                if row is None:
                    row = await self._legacy(project, actor, agent, request)
                if row is None:
                    if cancel is None:
                        raise NotFoundError("用例保存原请求")
                    row = self._new(project, actor, agent, cancel)
                    row.status = "preparing"
                    row.deadline_at = row.created_at
                    self.db.add(row)
                if row.agent_id != agent:
                    raise NotFoundError("用例保存原请求")
                if cancel is not None:
                    self._scope(row, agent, cancel)
                now = datetime.now(timezone.utc)
                if row.status == "preparing":
                    if cancel is not None:
                        row.status, row.error_code = "cancelled", "cancelled"
                        row.completed_at = now
                    elif row.deadline_at <= now:
                        row.status = "failed"
                        row.error_code = "preparation_expired"
                        row.completed_at = now
                await self.db.commit()
                return await self._view(row)
        except BaseException:
            await self.db.rollback()
            raise
