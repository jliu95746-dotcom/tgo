"""Require fresh owner definitions and all channel evidence for this employee."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
)
from app.models.agent import Agent, AgentToolAssociation
from app.models.agent_device_script_binding import AgentDeviceScriptBinding
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_activation_policy import AgentActivationPolicy
from app.models.agent_test_report import AgentTestReport
from app.models.internal import Agent as RuntimeAgent
from app.models.tool import Tool
from app.models.workflow import AgentWorkflow
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_activation_readiness import (
    ActivationBlocker,
    ActivationChannelCoverage,
    AgentActivationReadiness,
)
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities,
    KnowledgeCapabilityQuery,
)
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_activation_policies import (
    AgentActivationPolicyService,
    RequirementsOwner,
)
from app.services.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilityService,
)
from app.services.agent_service import AgentService
from app.services.agent_trial_fingerprint import configuration_fingerprint
from app.services.agent_trial_reader import AgentTrialReader
from app.services.agent_trial_reviews import AgentTrialReviewService
from app.services.api_service import api_service_client
from app.schemas.agent_business_reviews import AgentBusinessCapabilities
from app.services.agent_business_evidence_revision import (
    business_evidence_revision,
)
from app.schemas.agent_business_execution import (
    BusinessRuntimeQuery,
    BusinessRuntimeReadiness,
)
from app.services.workflow_service import workflow_service_client
from app.schemas.agent_device_script_capability import (
    ScriptRuntimeDefinition,
)
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
)
from app.services.agent_device_script_activation import (
    ScriptDefinitionReader,
    read_script_definition,
    script_activation_evidence,
)

ConfigurationReader = Callable[[UUID, UUID, UUID], Awaitable[RuntimeAgent]]
RuntimeReader = Callable[
    [BusinessRuntimeQuery], Awaitable[BusinessRuntimeReadiness]
]


class KnowledgeReader(Protocol):
    async def read(
        self,
        project: UUID,
        reviewer: UUID,
        agent: UUID,
        query: KnowledgeCapabilityQuery,
    ) -> AgentKnowledgeCapabilities:
        ...


class BusinessReader(Protocol):
    async def read(
        self, project: UUID, actor: UUID, agent: UUID
    ) -> AgentBusinessCapabilities:
        ...


class AgentActivationReadinessService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        owner: RequirementsOwner = api_service_client,
        configuration_reader: ConfigurationReader | None = None,
        knowledge: KnowledgeReader | None = None,
        business: BusinessReader | None = None,
        scripts: ScriptDefinitionReader | None = None,
        runtime: RuntimeReader = api_service_client.get_business_runtime_readiness,
    ) -> None:
        self.db, self.policies = db, AgentActivationPolicyService(db, owner)
        self.configuration = configuration_reader or self._configuration
        self.knowledge = knowledge or AgentKnowledgeCapabilityService(
            AgentTrialReviewService(db, AgentTrialReader(db)),
        )
        if business is None:
            from app.services.agent_business_capabilities import (
                AgentBusinessCapabilityService,
            )
            from app.services.agent_business_reviews import (
                AgentBusinessReviewService,
            )
            from app.services.agent_business_trials import (
                AgentBusinessTrialService,
            )

            business = AgentBusinessCapabilityService(
                db,
                AgentBusinessReviewService(db, AgentBusinessTrialService(db)),
            )
        self.business = business
        self.runtime = runtime
        self.scripts = scripts or self._script_definition

    async def _script_definition(
        self, selection: ScriptSelectionRecord
    ) -> ScriptRuntimeDefinition:
        return await read_script_definition(self.db, selection)

    async def _configuration(
        self, project: UUID, actor: UUID, agent: UUID
    ) -> RuntimeAgent:
        # A retained SQLAlchemy identity map must not mask another request's edits.
        await self.policies._agent(project, agent)
        async with AIServiceClient(AgentService(self.db), project) as reader:
            return await reader.get_trial_agent(
                agent, requested_by=actor, verify_runtime=False
            )

    @staticmethod
    def _policy_identity(
        rows: list[AgentActivationPolicy],
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                row.id,
                row.channel,
                row.template_id,
                row.template_version,
                row.revision,
                row.requirements_revision,
                row.deleted_at,
            )
            for row in rows
        )

    async def _unverified_dependencies(
        self,
        agent: Agent,
        *,
        business_supported: bool = False,
        workflow_supported: bool = False,
        script_supported: bool = False,
    ) -> bool:
        script = await self.db.scalar(
            select(AgentDeviceScriptBinding.id)
            .where(
                AgentDeviceScriptBinding.project_id == agent.project_id,
                AgentDeviceScriptBinding.agent_id == agent.id,
                AgentDeviceScriptBinding.enabled.is_(True),
                AgentDeviceScriptBinding.deleted_at.is_(None),
            )
            .limit(1)
        )
        if script is not None and not script_supported:
            return True
        business = await self.db.scalar(
            select(AgentBusinessConnection.id)
            .where(
                AgentBusinessConnection.project_id == agent.project_id,
                AgentBusinessConnection.agent_id == agent.id,
                AgentBusinessConnection.enabled.is_(True),
            )
            .limit(1)
        )
        workflow = await self.db.scalar(
            select(AgentWorkflow.id)
            .where(
                AgentWorkflow.agent_id == agent.id,
                AgentWorkflow.enabled.is_(True),
                AgentWorkflow.deleted_at.is_(None),
            )
            .limit(1)
        )
        tool = await self.db.scalar(
            select(AgentToolAssociation.id)
            .join(
                Tool,
                Tool.id == AgentToolAssociation.tool_id,
            )
            .where(
                AgentToolAssociation.agent_id == agent.id,
                AgentToolAssociation.enabled.is_(True),
                AgentToolAssociation.deleted_at.is_(None),
                Tool.project_id == agent.project_id,
                Tool.deleted_at.is_(None),
            )
            .limit(1)
        )
        # Knowledge trials suppress these paths, so they cannot authorize them.
        # The bound device has a separate live read test, checked below.
        return bool(
            agent.skills_enabled
            or agent.is_remote_store_agent
            or agent.agent_category != "normal"
            or (workflow and not workflow_supported)
            or tool
            or (business and not business_supported)
        )

    async def _verified_scenario_workflows(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        business: AgentBusinessCapabilities | None,
        kind: Literal["logistics", "aftersales"],
    ) -> bool:
        """Match fixed scenario graphs against current owner and business evidence."""
        rows = list(
            (
                await self.db.scalars(
                    select(AgentWorkflow)
                    .where(
                        AgentWorkflow.agent_id == agent_id,
                        AgentWorkflow.enabled.is_(True),
                        AgentWorkflow.deleted_at.is_(None),
                    )
                    .order_by(AgentWorkflow.workflow_id)
                    .limit(21)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if (
            not rows
            or len(rows) > 20
            or (kind == "logistics" and business is None)
        ):
            return False
        if len({row.workflow_id for row in rows}) != len(rows) or any(
            row.release_version is None
            or row.release_version < 1
            or not row.release_id
            or not row.release_digest
            for row in rows
        ):
            return False
        versions = {
            row.workflow_id: cast(int, row.release_version) for row in rows
        }
        snapshot = [
            (
                row.id,
                row.workflow_id,
                row.release_version,
                row.release_id,
                row.release_digest,
            )
            for row in rows
        ]
        now = datetime.now(UTC)
        reviewed: set[tuple[UUID, str]] = set()
        if kind == "logistics":
            assert business is not None
            if (
                business.project_id != project
                or business.agent_id != agent_id
                or business.checked_by != actor
                or business.scope != "employee_business_capabilities"
                or business.checked_at.tzinfo is None
                or not -30 <= (now - business.checked_at).total_seconds() <= 60
            ):
                return False
            reviewed = {
                (item.connection_id, item.operation)
                for item in business.capabilities
                if item.status == "passed"
                and item.report_id
                and item.review_id
                and not item.issues
                and item.valid_until is not None
                and item.valid_until.tzinfo is not None
                and item.valid_until > now
            }
        try:
            releases = (
                await workflow_service_client.resolve_published_bindings(
                    list(versions),
                    str(project),
                    versions,
                )
            )
        except Exception:
            return False
        if set(releases) != set(versions):
            return False
        for row in rows:
            release = releases[row.workflow_id]
            kinds, queries = (
                release.node_types,
                release.required_business_queries,
            )
            if (
                release.release_version != row.release_version
                or release.release_id != row.release_id
                or release.definition_digest != row.release_digest
                or not kinds
                or len(release.input_parameters) != 1
            ):
                return False
            parameter = release.input_parameters[0]
            if kind == "logistics":
                if (
                    not queries
                    or set(kinds)
                    - {"input", "condition", "business_query", "answer"}
                    or not {"input", "business_query", "answer"}.issubset(
                        kinds
                    )
                    or len(queries) != kinds.count("business_query")
                    or release.approval_actions not in (None, [])
                    or parameter.name != "order_no"
                    or parameter.type != "string"
                    or not parameter.required
                    or any(
                        (item.connection_id, item.operation) not in reviewed
                        for item in queries
                    )
                ):
                    return False
            elif (
                sorted(kinds) != ["answer", "approval", "input"]
                or queries != []
                or release.approval_actions != ["create_aftersales_review"]
                or parameter.name != "question"
                or parameter.type != "string"
                or not parameter.required
            ):
                return False
        current = list(
            (
                await self.db.scalars(
                    select(AgentWorkflow)
                    .where(
                        AgentWorkflow.agent_id == agent_id,
                        AgentWorkflow.enabled.is_(True),
                        AgentWorkflow.deleted_at.is_(None),
                    )
                    .order_by(AgentWorkflow.workflow_id)
                    .limit(21)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        return [
            (
                row.id,
                row.workflow_id,
                row.release_version,
                row.release_id,
                row.release_digest,
            )
            for row in current
        ] == snapshot

    async def _verified_device_binding(
        self,
        project: UUID,
        agent_id: UUID,
        device_id: str,
        policy_digest: str | None,
    ) -> bool:
        if not policy_digest:
            return False
        try:
            readiness = await AgentService(self.db).device_action_readiness(
                project, agent_id
            )
        except ExternalServiceError:
            return False
        return (
            readiness.agent_id == agent_id
            and readiness.status == "ready"
            and readiness.device_id == device_id
            and readiness.policy_digest == policy_digest
        )

    async def _reports_match(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        coverage: AgentKnowledgeCapabilities,
        fingerprint: str,
    ) -> bool:
        identifiers = [
            case.report_id
            for group in coverage.capabilities
            for case in group.cases
        ]
        if (
            not identifiers
            or None in identifiers
            or len(set(identifiers)) != len(identifiers)
        ):
            return False
        rows = list(
            (
                await self.db.scalars(
                    select(AgentTestReport)
                    .where(
                        AgentTestReport.id.in_(identifiers),
                        AgentTestReport.project_id == project,
                        AgentTestReport.created_by == actor,
                        AgentTestReport.agent_id == agent,
                        AgentTestReport.channel == coverage.channel.value,
                        AgentTestReport.deleted_at.is_(None),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        return len(rows) == len(identifiers) and all(
            row.configuration_fingerprint == fingerprint
            and row.status in {"completed", "blocked"}
            for row in rows
        )

    async def _channel(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        policy: AgentActivationPolicy,
        fingerprint: str,
        blockers: list[ActivationBlocker],
        business: AgentBusinessCapabilities | None = None,
        script: ScriptRuntimeDefinition | None = None,
    ) -> ActivationChannelCoverage:
        selection = self.policies._selection(policy)
        row = ActivationChannelCoverage(
            **selection.model_dump(),
            policy_revision=policy.revision,
            requirements_revision=policy.requirements_revision,
            status="unavailable",
        )
        requirements = await self.policies._requirements(
            project, actor, selection, agent
        )
        if policy.requirements_revision != requirements.requirements_revision:
            blockers.append(
                ActivationBlocker(
                    code="requirements_changed",
                    channel=selection.channel,
                    message="岗位要求已更新，请重新确认配置并测试",
                )
            )
            return row
        if (
            selection.template_id == "assistant"
            and selection.channel == KnowledgeChannel.INTERNAL
            and set(requirements.required_resources) == {"model"}
            and set(requirements.required_capabilities)
            == {"business_data_access"}
            and not requirements.knowledge_cases
            and script is not None
        ):
            row.status, row.valid_until = "passed", script.valid_until
            if script.remaining_runs == 0:
                blockers.append(
                    ActivationBlocker(
                        code="script_capacity_exhausted",
                        channel=selection.channel,
                        resource="device",
                        message="脚本剩余次数已用完，暂不能启动新任务；原任务结果仍可核验",
                    )
                )
            return row
        if (
            selection.template_id == "logistics"
            and set(requirements.required_resources) == {"model", "connection"}
            and set(requirements.required_capabilities)
            == {"scoped_order_query", "scoped_logistics_query"}
            and not requirements.knowledge_cases
        ):
            if (
                business is None
                or business.project_id != project
                or business.agent_id != agent
                or business.checked_by != actor
                or business.scope != "employee_business_capabilities"
                or business.checked_at.tzinfo is None
                or not -30
                <= (datetime.now(UTC) - business.checked_at).total_seconds()
                <= 60
            ):
                blockers.append(
                    ActivationBlocker(
                        code="tests_not_current",
                        channel=selection.channel,
                        resource="connection",
                        message="当前订单和物流的业务评审依据无法核验，请重新检查",
                    )
                )
                return row
            row.business_capabilities = list(business.capabilities)
            now = datetime.now(UTC)
            if not {"order_query", "logistics_query"}.issubset(
                {item.operation for item in business.capabilities}
            ) or any(
                item.status != "passed"
                or item.report_id is None
                or item.review_id is None
                or item.issues
                or item.valid_until is None
                or item.valid_until.tzinfo is None
                or item.valid_until <= now
                for item in business.capabilities
            ):
                blockers.append(
                    ActivationBlocker(
                        code="tests_not_current",
                        channel=selection.channel,
                        resource="connection",
                        message="订单查询和物流查询需要分别完成当前业务测试与评审，请补齐未通过的操作",
                    )
                )
                row.status = "needs_retest"
                return row
            row.status = "passed"
            row.valid_until = min(
                item.valid_until
                for item in business.capabilities
                if item.valid_until is not None
            )
            return row
        if (
            set(requirements.required_resources) != {"model", "knowledge"}
            or set(requirements.required_capabilities)
            != {"governed_knowledge_search"}
            or not requirements.knowledge_cases
        ):
            blockers.append(
                ActivationBlocker(
                    code="dependency_not_verified",
                    channel=selection.channel,
                    message="该岗位需要的业务能力尚未完成验证",
                )
            )
            return row
        coverage = await self.knowledge.read(
            project,
            actor,
            agent,
            KnowledgeCapabilityQuery(
                channel=selection.channel, cases=requirements.knowledge_cases
            ),
        )
        if (
            coverage.agent_id != agent
            or coverage.reviewed_by != actor
            or coverage.channel != selection.channel
            or coverage.knowledge_status != "available"
            or len(coverage.capabilities) != 1
        ):
            blockers.append(
                ActivationBlocker(
                    code="knowledge_not_ready",
                    channel=selection.channel,
                    resource="knowledge",
                    message="当前渠道的知识依据无法确认，请补齐配置后重测",
                )
            )
            return row
        group = coverage.capabilities[0]
        expected = {
            case.id: case.model_dump(mode="json")
            for case in requirements.knowledge_cases
        }
        actual = {
            case.case.id: case.case.model_dump(mode="json")
            for case in group.cases
        }
        now = datetime.now(UTC)
        expiry = group.valid_until
        complete = (
            expected == actual
            and group.template_id == selection.template_id
            and group.template_version == selection.template_version
            and group.status == "passed"
            and expiry is not None
            and expiry.tzinfo is not None
            and expiry > now
            and all(
                case.status == "passed"
                and case.valid_until is not None
                and case.valid_until.tzinfo is not None
                and case.valid_until > now
                for case in group.cases
            )
            and await self._reports_match(
                project, actor, agent, coverage, fingerprint
            )
        )
        if not complete:
            blockers.append(
                ActivationBlocker(
                    code="tests_not_current",
                    channel=selection.channel,
                    message="必测问题尚未全部通过，或测试已失效，请重新测试和评审",
                )
            )
            row.status = (
                "needs_retest" if group.status == "passed" else group.status
            )
            return row
        expiries = [
            case.valid_until
            for case in group.cases
            if case.valid_until is not None
        ]
        row.status, row.valid_until = "passed", min(expiries)
        return row

    async def read(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        *,
        channel: KnowledgeChannel | None = None,
    ) -> AgentActivationReadiness:
        stored = await self.policies._agent(project, agent_id)
        result = AgentActivationReadiness(
            project_id=project,
            agent_id=agent_id,
            checked_by=actor,
            checked_at=datetime.now(UTC),
            enabled=stored.is_active,
            can_enable=False,
        )
        try:
            async with asyncio.timeout(15):
                await self._read(
                    project, actor, agent_id, stored, result, channel
                )
        except AuthorizationError:
            raise
        except ConflictError:
            result.blockers.append(
                ActivationBlocker(
                    code="configuration_unavailable",
                    message="员工或模型配置已变化，请重新读取后测试",
                )
            )
        except Exception:
            result.blockers.append(
                ActivationBlocker(
                    code="owner_unavailable", message="当前配置和测试依据暂不可核验，请稍后重新检查"
                )
            )
        result.checked_at = datetime.now(UTC)
        for row in result.channels:
            if (
                row.business_capabilities
                and row.status == "passed"
                and (
                    row.valid_until is None
                    or row.valid_until <= result.checked_at
                )
            ):
                row.status, row.valid_until = "needs_retest", None
                result.blockers.append(
                    ActivationBlocker(
                        code="tests_not_current",
                        channel=row.channel,
                        resource="connection",
                        message="业务评审依据已到期，请重新测试和评审",
                    )
                )
        result.can_enable = (
            not result.blockers
            and bool(result.channels)
            and all(
                row.status == "passed"
                and row.valid_until is not None
                and row.valid_until > result.checked_at
                for row in result.channels
            )
        )
        return AgentActivationReadiness.model_validate(result.model_dump())

    async def _read(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        stored: Agent,
        result: AgentActivationReadiness,
        channel: KnowledgeChannel | None = None,
    ) -> None:
        initial = await self.configuration(project, actor, agent_id)
        if (
            initial.id != agent_id
            or initial.project_id != str(project)
            or not initial.model_owner_revision
        ):
            result.blockers.append(
                ActivationBlocker(
                    code="configuration_unavailable",
                    resource="model",
                    message="当前模型配置尚未核验，请补齐模型配置后重测",
                )
            )
            return
        result.model_owner_revision = initial.model_owner_revision
        fingerprint = configuration_fingerprint(initial)
        result.configuration_fingerprint = fingerprint
        initial_device_id = stored.bound_device_id
        initial_device_policy_digest = stored.bound_device_policy_digest
        result.business_evidence_revision = await business_evidence_revision(
            self.db, project, actor, agent_id
        )
        policies = [
            row
            for row in await self.policies._rows(project, agent_id)
            if row.deleted_at is None
            and (channel is None or row.channel == channel.value)
        ]
        identity = self._policy_identity(policies)
        if not policies:
            result.blockers.append(
                ActivationBlocker(
                    code="policy_missing", message="请选择员工岗位和服务渠道"
                )
            )
        script = await script_activation_evidence(
            project, actor, initial, policies, self.scripts
        )
        business_supported = False
        if policies and all(
            row.template_id == "logistics" for row in policies
        ):
            runtime = await self.runtime(
                BusinessRuntimeQuery(project_id=project, requested_by=actor)
            )
            business_supported = (
                runtime.project_id == project
                and runtime.requested_by == actor
                and runtime.available
                and runtime.scope == "employee_business_runtime"
                and not runtime.can_enable
                and runtime.protocol_version == 2
                and set(runtime.operations)
                == {
                    "order_query",
                    "logistics_query",
                    "customer_profile_query",
                    "membership_query",
                }
                and runtime.checked_at.tzinfo is not None
                and -5
                <= (datetime.now(UTC) - runtime.checked_at).total_seconds()
                <= 30
            )
        business = (
            await self.business.read(project, actor, agent_id)
            if any(row.template_id == "logistics" for row in policies)
            else None
        )
        workflow_supported = (
            business_supported
            and await self._verified_scenario_workflows(
                project, actor, agent_id, business, "logistics"
            )
        ) or (
            bool(policies)
            and all(row.template_id == "aftersales" for row in policies)
            and await self._verified_scenario_workflows(
                project, actor, agent_id, None, "aftersales"
            )
        )
        if await self._unverified_dependencies(
            stored,
            business_supported=business_supported,
            workflow_supported=workflow_supported,
            script_supported=script is not None,
        ):
            result.blockers.append(
                ActivationBlocker(
                    code="dependency_not_verified",
                    message="员工绑定的业务连接、工具、流程或技能尚未接通正式执行核验，请完成业务接入后启用",
                )
            )
        for policy in policies:
            result.channels.append(
                await self._channel(
                    project,
                    actor,
                    agent_id,
                    policy,
                    fingerprint,
                    result.blockers,
                    business,
                    script,
                )
            )
        current = await self.configuration(project, actor, agent_id)
        current_policies = [
            row
            for row in await self.policies._rows(project, agent_id)
            if row.deleted_at is None
            and (channel is None or row.channel == channel.value)
        ]
        if (
            configuration_fingerprint(current)
            != result.configuration_fingerprint
            or self._policy_identity(current_policies) != identity
            or await business_evidence_revision(
                self.db, project, actor, agent_id
            )
            != result.business_evidence_revision
        ):
            result.blockers.append(
                ActivationBlocker(
                    code="tests_not_current",
                    message="检查期间员工配置或业务评审依据发生变化，请重新检查",
                )
            )
            for row in result.channels:
                if row.business_capabilities:
                    row.status, row.valid_until = "needs_retest", None
        for policy in current_policies:
            requirements = await self.policies._requirements(
                project, actor, self.policies._selection(policy), agent_id
            )
            if (
                requirements.requirements_revision
                != policy.requirements_revision
            ):
                result.blockers.append(
                    ActivationBlocker(
                        code="requirements_changed",
                        channel=KnowledgeChannel(policy.channel),
                        message="检查期间岗位要求发生变化，请重新检查",
                    )
                )
        if (
            initial.bound_device_id != initial_device_id
            or initial.bound_device_policy_digest
            != initial_device_policy_digest
        ):
            result.blockers.append(
                ActivationBlocker(
                    code="configuration_unavailable",
                    resource="device",
                    message="设备绑定与员工测试配置不一致，请重新读取后测试",
                )
            )
        elif initial_device_id:
            if not await self._verified_device_binding(
                project,
                agent_id,
                initial_device_id,
                initial_device_policy_digest,
            ):
                result.blockers.append(
                    ActivationBlocker(
                        code="dependency_not_verified",
                        resource="device",
                        message="当前设备读取策略未在本次连接上测试通过，请重新测试设备",
                    )
                )
