"""Serialize exact approvals without granting employee execution."""

import hmac
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.exceptions import ConflictError, NotFoundError
from app.models.agent_device_script_selection import (
    AgentDeviceScriptSelection,
    AgentDeviceScriptSelectionRequest,
)
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
    ScriptSelectionState,
    ScriptSelectionWrite,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_reviews import AgentDeviceScriptReviews
from app.services.agent_trial_fingerprint import private_digest
from app.services.agent_device_script_selection_identity import (
    selection_record, selection_signature, verified_selection, selection_row,
)


class ScriptSelectionService:
    def __init__(self, cases: AgentDeviceScriptCaseService) -> None:
        self.cases, self.db = cases, cases.db
        self.reviews = AgentDeviceScriptReviews(cases)

    record = staticmethod(selection_record)
    _signature = staticmethod(selection_signature)
    _verified = staticmethod(verified_selection)

    async def _row(
        self,
        project: UUID,
        agent: UUID,
    ) -> AgentDeviceScriptSelection | None:
        return await selection_row(self.db, project, agent)

    @staticmethod
    def _receipt_signature(
        row: AgentDeviceScriptSelectionRequest,
        result: ScriptSelectionRecord,
    ) -> str:
        return private_digest(
            {
                "purpose": "employee-script-selection-write-v1",
                "project": str(row.project_id),
                "actor": str(row.requested_by),
                "agent": str(row.agent_id),
                "request": str(row.request_id),
                "command_digest": row.command_digest,
                "result": result.model_dump(mode="json"),
            }
        )

    async def _request(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> tuple[
        AgentDeviceScriptSelectionRequest, ScriptSelectionRecord
    ] | None:
        row: AgentDeviceScriptSelectionRequest | None = await self.db.scalar(
            select(AgentDeviceScriptSelectionRequest)
            .where(
                AgentDeviceScriptSelectionRequest.project_id == project,
                AgentDeviceScriptSelectionRequest.requested_by == actor,
                AgentDeviceScriptSelectionRequest.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        try:
            result = ScriptSelectionRecord.model_validate_json(row.result_json)
            if (
                row.deleted_at is not None
                or row.agent_id != agent
                or result.project_id != project
                or result.agent_id != agent
                or result.configured_by != actor
                or result.request_id != request
                or not hmac.compare_digest(
                    row.result_digest,
                    self._receipt_signature(row, result),
                )
            ):
                raise ValueError("Original selection receipt changed")
            return row, result
        except (ValueError, ValidationError):
            raise ConflictError("原审批选择请求无法核验") from None

    async def request_result(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptSelectionRecord:
        await self.cases._authorize(project, actor, agent)
        found = await self._request(project, actor, agent, request)
        if found is None:
            raise NotFoundError("固定审批选择请求")
        return found[1]

    async def _state(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        row: AgentDeviceScriptSelection | None,
    ) -> ScriptSelectionState:
        value = self._verified(row) if row is not None else None
        status: str = "unselected"
        if value is not None and value.enabled:
            status = "changed"
            binding = await self.cases.trials.bindings._binding(project, agent)
            if (
                value.configured_by == actor
                and binding is not None
                and binding.enabled
                and binding.configured_by == actor
                and binding.id == value.binding_id
                and binding.revision == value.binding_revision
            ):
                try:
                    trial = await self.cases.trials._row(
                        project,
                        actor,
                        agent,
                        report=value.report_id,
                    )
                    review = await self.reviews._review(trial)
                    reviewed = await self.reviews._view(trial, review)
                    if review is not None and review.id == value.review_id:
                        if reviewed.status == "revoked":
                            status = "revoked"
                        elif (
                            reviewed.status == "expired"
                            or value.expires_at <= datetime.now(timezone.utc)
                        ):
                            status = "expired"
                        elif reviewed.status == "approved" and (
                            trial.binding_id == binding.id
                            and trial.binding_revision == binding.revision
                            and trial.policy_digest == binding.policy_digest
                            and trial.grant_digest == binding.grant_digest
                        ):
                            status = "selected"
                except (ConflictError, NotFoundError):
                    status = "changed"
        return ScriptSelectionState.model_validate(
            {
                "project_id": project,
                "actor_id": actor,
                "agent_id": agent,
                "selection": value,
                "status": status,
            }
        )

    async def read(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
    ) -> ScriptSelectionState:
        await self.cases._authorize(project, actor, agent)
        return await self._state(
            project,
            actor,
            agent,
            await self._row(project, agent),
        )

    async def save(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptSelectionWrite,
    ) -> ScriptSelectionRecord:
        command = ScriptSelectionWrite.model_validate_json(
            command.model_dump_json()
        )
        try:
            result = await self._save(project, actor, agent, command)
            await self.db.commit()
            return result
        except BaseException:
            await self.db.rollback()
            raise

    async def _save(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptSelectionWrite,
    ) -> ScriptSelectionRecord:
        await self.cases._authorize(project, actor, agent)
        await lock_account_agents(self.db, project)
        digest = private_digest(
            {
                "agent": str(agent),
                "command": command.model_dump(mode="json"),
            }
        )
        old = await self._request(project, actor, agent, command.request_id)
        if old is not None:
            if old[0].command_digest != digest:
                raise ConflictError("原审批选择请求内容不一致")
            return old[1]
        employee = await self.cases.trials.bindings._agent(project, agent)
        if employee.is_active:
            raise ConflictError("请先停用员工，再修改固定审批选择")
        row = await self._row(project, agent)
        if (
            row.revision if row is not None else 0
        ) != command.expected_revision:
            raise ConflictError("固定审批选择已有新版本，请重新读取")
        now = datetime.now(timezone.utc)
        if command.enabled:
            assert (
                command.report_id is not None and command.review_id is not None
            )
            trial = await self.cases.trials._row(
                project,
                actor,
                agent,
                report=command.report_id,
            )
            review = await self.reviews._review(trial)
            state = await self.reviews._view(trial, review)
            binding = await self.cases.trials.bindings._binding(project, agent)
            if (
                state.status != "approved"
                or review is None
                or review.id != command.review_id
                or binding is None
                or not binding.enabled
                or binding.configured_by != actor
                or binding.id != trial.binding_id
                or binding.revision != trial.binding_revision
                or binding.policy_digest != trial.policy_digest
                or binding.grant_digest != trial.grant_digest
                or employee.bound_device_id
                or employee.bound_device_policy_digest
            ):
                raise ConflictError("请选择当前脚本绑定的有效批准报告")
            _, expires = await self.reviews._proof(trial)
            if row is None:
                row = AgentDeviceScriptSelection(
                    id=uuid4(),
                    project_id=project,
                    agent_id=agent,
                    revision=0,
                )
                self.db.add(row)
            row.binding_id, row.binding_revision = binding.id, binding.revision
            row.report_id, row.review_id = command.report_id, command.review_id
            row.expires_at = min(expires, review.expires_at)
        elif row is None:
            raise NotFoundError("固定审批选择")
        assert row is not None
        row.revision += 1
        row.enabled, row.configured_by = command.enabled, actor
        row.request_id, row.saved_at = command.request_id, now
        row.selection_digest = "0" * 64
        row.selection_digest = self._signature(self.record(row))
        result = self._verified(row)
        await self.cases.trials.bindings._actor(project, actor)
        receipt = AgentDeviceScriptSelectionRequest(
            project_id=project,
            agent_id=agent,
            requested_by=actor,
            request_id=command.request_id,
            command_digest=digest,
            result_json=result.model_dump_json(),
        )
        receipt.result_digest = self._receipt_signature(receipt, result)
        self.db.add(receipt)
        await self.db.flush()
        return result
