"""Saved immutable cases reuse the existing paused-employee script boundary."""

import asyncio
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.config import settings
from app.exceptions import ConflictError, NotFoundError
from app.exceptions import ValidationError as RequestError
from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.models.agent_device_script_binding import AgentDeviceScriptBinding
from app.schemas.agent_device_script_cases import (
    ScriptCasePage,
    ScriptCaseRead,
    ScriptCaseRecord,
    ScriptCaseValues,
    ScriptCaseWrite,
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
    ScriptTrialEvidence,
)
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_trials import ScriptTrialReport
from app.schemas.device_script_execution import (
    validate_reviewed_script_configuration,
    validate_script_values,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import private_digest
from app.services.script_evidence_vault import ScriptEvidenceVault

if TYPE_CHECKING:
    from app.services.agent_device_script_trials import (
        AgentDeviceScriptTrialService,
    )


class AgentDeviceScriptCaseService:
    def __init__(self, trials: "AgentDeviceScriptTrialService") -> None:
        self.trials, self.db = trials, trials.db

    async def _authorize(
        self, project: UUID, actor: UUID, agent: UUID
    ) -> None:
        await self.trials.bindings._actor(project, actor)
        await self.trials.bindings._agent(project, agent)

    async def _version(
        self,
        project: UUID,
        agent: UUID,
        version: UUID,
    ) -> AgentDeviceScriptCase:
        row: AgentDeviceScriptCase | None = await self.db.scalar(
            select(AgentDeviceScriptCase)
            .where(
                AgentDeviceScriptCase.id == version,
                AgentDeviceScriptCase.project_id == project,
                AgentDeviceScriptCase.agent_id == agent,
                AgentDeviceScriptCase.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("员工脚本用例版本")
        return row

    async def _latest(self, row: AgentDeviceScriptCase) -> int:
        latest: int | None = await self.db.scalar(
            select(func.max(AgentDeviceScriptCase.revision)).where(
                AgentDeviceScriptCase.project_id == row.project_id,
                AgentDeviceScriptCase.agent_id == row.agent_id,
                AgentDeviceScriptCase.case_id == row.case_id,
                AgentDeviceScriptCase.deleted_at.is_(None),
            )
        )
        return latest or 0

    @staticmethod
    def _digest(row: AgentDeviceScriptCase, values: ScriptCaseValues) -> str:
        if len(settings.secret_key) < 32 or settings.secret_key.startswith(
            "your-"
        ):
            raise ConflictError("请先配置服务端签名密钥")
        return private_digest(
            {
                **{
                    name: str(getattr(row, name))
                    for name in (
                        "id",
                        "project_id",
                        "agent_id",
                        "created_by",
                        "request_id",
                        "case_id",
                        "binding_id",
                        "device_id",
                    )
                },
                "revision": row.revision,
                "binding_revision": row.binding_revision,
                "title": row.title,
                "policy_digest": row.policy_digest,
                "grant_digest": row.grant_digest,
                "created_at": row.created_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "values": values.model_dump(mode="json"),
            }
        )

    def _values(self, row: AgentDeviceScriptCase) -> ScriptCaseValues:
        if (
            row.ciphertext is None
            or row.cleared_at is not None
            or (row.expires_at <= datetime.now(timezone.utc))
        ):
            raise ConflictError("用例正文已到期，请建立新版本")
        raw = ScriptEvidenceVault().decrypt(
            row.project_id,
            row.created_by,
            row.agent_id,
            row.id,
            "case",
            row.ciphertext,
        )
        try:
            values = ScriptCaseValues.model_validate_json(raw)
            if not hmac.compare_digest(
                row.case_digest, self._digest(row, values)
            ):
                raise ValueError("Stored case changed")
            return values
        except ValueError:
            raise ConflictError("用例版本未通过完整性核验") from None

    async def _record(
        self,
        row: AgentDeviceScriptCase,
        *,
        latest: int | None = None,
        binding: AgentDeviceScriptBinding | None = None,
    ) -> ScriptCaseRecord:
        if latest is None:
            latest = await self._latest(row)
        if binding is None:
            binding = await self.trials.bindings._binding(
                row.project_id, row.agent_id
            )
        state = "current"
        if row.expires_at <= datetime.now(timezone.utc) or row.cleared_at:
            state = "expired"
        elif row.revision != latest:
            state = "superseded"
        elif (
            binding is None
            or not binding.enabled
            or binding.id != row.binding_id
            or binding.revision != row.binding_revision
            or binding.device_id != row.device_id
            or binding.policy_digest != row.policy_digest
            or binding.grant_digest != row.grant_digest
        ):
            state = "changed"
        return ScriptCaseRecord.model_validate(
            {
                **{
                    name: getattr(row, name)
                    for name in (
                        "id",
                        "case_id",
                        "project_id",
                        "agent_id",
                        "created_by",
                        "request_id",
                        "revision",
                        "title",
                        "binding_id",
                        "binding_revision",
                        "device_id",
                        "policy_digest",
                        "grant_digest",
                        "case_digest",
                        "created_at",
                        "expires_at",
                    )
                },
                "status": state,
            }
        )

    async def _write_receipt(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: ScriptCaseWrite,
        digest: str,
    ) -> AgentDeviceScriptCase | None:
        row: AgentDeviceScriptCase | None = await self.db.scalar(
            select(AgentDeviceScriptCase).where(
                AgentDeviceScriptCase.project_id == project,
                AgentDeviceScriptCase.created_by == actor,
                AgentDeviceScriptCase.request_id == request.request_id,
            )
        )
        if row is not None and (
            row.agent_id != agent
            or not hmac.compare_digest(row.command_digest, digest)
        ):
            raise ConflictError("用例保存原编号已用于其他内容")
        return row

    async def save(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: ScriptCaseWrite,
    ) -> ScriptCaseRecord:
        from app.services.agent_device_script_case_requests import (
            ScriptCaseRequests,
        )

        request = ScriptCaseWrite.model_validate_json(
            request.model_dump_json()
        )
        requests = ScriptCaseRequests(self)
        digest = private_digest(
            {
                "agent_id": str(agent),
                "command": request.model_dump(mode="json"),
            }
        )
        pending, created = await requests.register(
            project, actor, agent, request, digest
        )
        if not created:
            if pending.status == "saved" and pending.version_id is not None:
                return await self._record(
                    await self._version(project, agent, pending.version_id)
                )
            raise ConflictError("原用例保存尚未完成或已终止，请查询原请求")
        try:
            async with asyncio.timeout(requests.preparation_seconds):
                return await self._save_registered(
                    project, actor, agent, request
                )
        except BaseException:
            await self.db.rollback()
            try:
                await requests.fail(project, actor, request.request_id)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Script case save settlement unavailable; "
                    "recover original request"
                )
            raise

    async def _save_registered(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: ScriptCaseWrite,
    ) -> ScriptCaseRecord:
        request = ScriptCaseWrite.model_validate_json(
            request.model_dump_json()
        )
        digest = private_digest(
            {
                "agent_id": str(agent),
                "command": request.model_dump(mode="json"),
            }
        )
        await self._authorize(project, actor, agent)
        existing = await self._write_receipt(
            project, actor, agent, request, digest
        )
        if existing is not None:
            return await self._record(existing)
        binding = await self.trials._binding(
            project, actor, agent, request.binding_id, request.binding_revision
        )
        device, policy, grant = (
            binding.device_id,
            binding.policy_digest,
            binding.grant_digest,
        )
        await self.db.commit()
        (
            bound,
            descriptor,
        ) = await self.trials.device.validate_script_configuration(
            project, device
        )
        bound, descriptor = validate_reviewed_script_configuration(
            project, device, bound, descriptor
        )
        if bound.policy.digest != policy or descriptor.grant_digest != grant:
            raise ConflictError("设备脚本范围已变化")
        try:
            values = ScriptCaseValues(
                parameters=dict(
                    validate_script_values(
                        descriptor.parameters, request.parameters, input=True
                    )
                ),
                expected_fields=dict(
                    validate_script_values(
                        descriptor.result_fields,
                        request.expected_fields,
                        input=False,
                    )
                ),
            )
        except (ValueError, TypeError):
            raise RequestError("请按已审核字段填写用例输入和预期结果") from None
        # Validate current authority before waiting on the local project lock.
        await self._authorize(project, actor, agent)
        await lock_account_agents(self.db, project)
        try:
            existing = await self._write_receipt(
                project, actor, agent, request, digest
            )
            if existing is not None:
                await self.db.commit()
                return await self._record(existing)
            employee = await self.trials.bindings._agent(project, agent)
            current_binding = await self.trials.bindings._binding(
                project, agent
            )
            if (
                employee.is_active
                or current_binding is None
                or not current_binding.enabled
                or current_binding.id != request.binding_id
                or current_binding.revision != request.binding_revision
                or current_binding.device_id != device
                or current_binding.policy_digest != policy
                or current_binding.grant_digest != grant
            ):
                raise ConflictError("员工或脚本配置已变化")
            if request.case_id is not None:
                latest: int | None = await self.db.scalar(
                    select(func.max(AgentDeviceScriptCase.revision)).where(
                        AgentDeviceScriptCase.project_id == project,
                        AgentDeviceScriptCase.agent_id == agent,
                        AgentDeviceScriptCase.case_id == request.case_id,
                    )
                )
                if latest != request.expected_revision:
                    raise ConflictError("用例已有新版本，请刷新后再保存")
            now = datetime.now(timezone.utc)
            row = AgentDeviceScriptCase(
                id=uuid4(),
                case_id=request.case_id or uuid4(),
                project_id=project,
                agent_id=agent,
                created_by=actor,
                request_id=request.request_id,
                revision=request.expected_revision + 1,
                title=request.title.strip(),
                binding_id=current_binding.id,
                binding_revision=current_binding.revision,
                device_id=device,
                policy_digest=policy,
                grant_digest=grant,
                command_digest=digest,
                case_digest="",
                created_at=now,
                expires_at=now
                + timedelta(days=settings.script_trial_body_retention_days),
            )
            row.case_digest = self._digest(row, values)
            row.ciphertext = ScriptEvidenceVault().encrypt(
                project, actor, agent, row.id, "case", values.model_dump_json()
            )
            self.db.add(row)
            await self.db.flush()
            from app.services.agent_device_script_case_requests import (
                ScriptCaseRequests,
            )

            await ScriptCaseRequests(self).attach(
                project, actor, agent, request, row.id
            )
            await self.db.commit()
            return await self._record(row)
        except BaseException:
            await self.db.rollback()
            raise

    async def get(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        version: UUID,
    ) -> ScriptCaseRead:
        await self._authorize(project, actor, agent)
        row = await self._version(project, agent, version)
        return ScriptCaseRead(
            case=await self._record(row), values=self._values(row)
        )

    async def save_state(
        self, project: UUID, actor: UUID, agent: UUID, request: UUID
    ) -> ScriptCaseSaveState:
        from app.services.agent_device_script_case_requests import (
            ScriptCaseRequests,
        )

        return await ScriptCaseRequests(self).state(
            project, actor, agent, request
        )

    async def cancel_save(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        control: ScriptCaseSaveControl,
    ) -> ScriptCaseSaveState:
        from app.services.agent_device_script_case_requests import (
            ScriptCaseRequests,
        )

        return await ScriptCaseRequests(self).cancel(
            project, actor, agent, control
        )

    async def by_request(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptCaseRecord:
        await self._authorize(project, actor, agent)
        row: AgentDeviceScriptCase | None = await self.db.scalar(
            select(AgentDeviceScriptCase).where(
                AgentDeviceScriptCase.project_id == project,
                AgentDeviceScriptCase.created_by == actor,
                AgentDeviceScriptCase.agent_id == agent,
                AgentDeviceScriptCase.request_id == request,
            )
        )
        if row is None:
            raise NotFoundError("用例保存原请求")
        return await self._record(row)

    async def list(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> ScriptCasePage:
        if not 0 <= skip <= 100000 or not 1 <= limit <= 100:
            raise ConflictError("用例分页范围无效")
        await self._authorize(project, actor, agent)
        table = AgentDeviceScriptCase
        ranking = (
            select(
                table.id,
                func.row_number()
                .over(
                    partition_by=table.case_id, order_by=table.revision.desc()
                )
                .label("position"),
            )
            .where(
                table.project_id == project,
                table.agent_id == agent,
                table.deleted_at.is_(None),
            )
            .subquery()
        )
        query = (
            select(table)
            .join(ranking, table.id == ranking.c.id)
            .where(ranking.c.position == 1)
        )
        total: int = (
            await self.db.scalar(
                select(func.count()).select_from(query.subquery())
            )
            or 0
        )
        rows = (
            await self.db.scalars(
                query.order_by(table.created_at.desc(), table.id.desc())
                .offset(skip)
                .limit(limit)
            )
        ).all()
        binding = await self.trials.bindings._binding(project, agent)
        return ScriptCasePage(
            project_id=project,
            agent_id=agent,
            skip=skip,
            limit=limit,
            total=total,
            items=[
                await self._record(row, latest=row.revision, binding=binding)
                for row in rows
            ],
        )

    async def run(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        version: UUID,
        command: ScriptTrialControl,
    ) -> ScriptTrialReport:
        from app.services.agent_device_script_case_runs import run_case

        return await run_case(self, project, actor, agent, version, command)

    async def evidence(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
    ) -> ScriptTrialEvidence:
        from app.services.agent_device_script_evidence import read_evidence

        return await read_evidence(self, project, actor, agent, report)

    async def purge_expired(self) -> int:
        from app.services.script_evidence_retention import (
            purge_script_evidence,
        )

        return await purge_script_evidence(self.db)
