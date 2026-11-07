"""A configuration switch and its immutable result share one database transaction."""
import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)
from app.models import Agent, AgentActivationRequestRecord
from app.schemas.agent import AgentUpdate
from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
    ActivationFailure,
)
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_activation_guard import ActivationConfirmationConflict
from app.services.agent_service import AgentService
from app.services.api_service import api_service_client


class ActorOwner(Protocol):
    async def get_agent_configuration_actor(
        self, query: AgentConfigurationActorQuery
    ) -> AgentConfigurationActor:
        ...


def command_digest(command: AgentActivationRequest) -> str:
    payload = command.model_dump(mode="json")
    if payload["expected"].get("business_evidence_revision") is None:
        # Keep pre-business knowledge confirmations retryable with their original digest.
        payload["expected"].pop("business_evidence_revision", None)
    payload["expected"]["channels"] = sorted(
        payload["expected"]["channels"], key=lambda row: row["channel"]
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AgentActivationRequestService:
    def __init__(
        self, db: AsyncSession, owner: ActorOwner = api_service_client
    ):
        self.db, self.owner = db, owner

    async def _access(self, project: UUID, actor: UUID) -> None:
        receipt = await self.owner.get_agent_configuration_actor(
            AgentConfigurationActorQuery(
                project_id=project, requested_by=actor
            )
        )
        if receipt.project_id != project or receipt.requested_by != actor:
            raise AuthorizationError("当前配置权限范围不一致")

    async def _row(
        self, project: UUID, actor: UUID, key: UUID
    ) -> AgentActivationRequestRecord | None:
        row = await self.db.scalar(
            select(AgentActivationRequestRecord)
            .where(
                AgentActivationRequestRecord.project_id == project,
                AgentActivationRequestRecord.requested_by == actor,
                AgentActivationRequestRecord.request_key == key,
            )
            .execution_options(populate_existing=True)
        )
        if row is not None and not isinstance(
            row, AgentActivationRequestRecord
        ):
            raise ExternalServiceError("tgo-ai", "启用回执类型无法确认")
        return row

    async def _agent(self, project: UUID, agent_id: UUID) -> None:
        agent = await self.db.scalar(
            select(Agent)
            .where(
                Agent.id == agent_id,
                Agent.project_id == project,
                Agent.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if agent is None:
            raise NotFoundError("员工")

    def _receipt(
        self, row: AgentActivationRequestRecord
    ) -> AgentActivationReceipt:
        try:
            command = AgentActivationRequest.model_validate_json(
                row.command_json
            )
            if (
                command.request_key != row.request_key
                or command_digest(command) != row.command_digest
            ):
                raise ValueError("Corrupt activation intent")
            finished = row.finished_at
            if finished is not None and finished.tzinfo is None:
                finished = finished.replace(
                    tzinfo=UTC
                )  # SQLite stores the UTC column without tzinfo.
            return AgentActivationReceipt.model_validate(
                {
                    "project_id": row.project_id,
                    "requested_by": row.requested_by,
                    "agent_id": row.agent_id,
                    "request_key": row.request_key,
                    "status": row.status,
                    "command": command,
                    "finished_at": finished,
                    "failure_code": row.failure_code,
                }
            )
        except (SchemaValidationError, ValueError):
            raise ExternalServiceError(
                "tgo-ai", "原启用回执无法确认，请联系管理员核查"
            ) from None

    async def lookup(
        self, project: UUID, actor: UUID, agent_id: UUID, key: UUID
    ) -> AgentActivationReceipt:
        await self._access(project, actor)
        row = await self._row(project, actor, key)
        if row is not None:
            if row.agent_id != agent_id:
                raise NotFoundError("启用请求")
            return self._receipt(row)
        await self._agent(project, agent_id)
        return AgentActivationReceipt(
            project_id=project,
            requested_by=actor,
            agent_id=agent_id,
            request_key=key,
            status="not_found",
        )

    async def submit(
        self,
        project: UUID,
        actor: UUID,
        agent_id: UUID,
        command: AgentActivationRequest,
    ) -> AgentActivationReceipt:
        try:
            await self._access(project, actor)
            await lock_account_agents(self.db, project)
            row = await self._row(project, actor, command.request_key)
            if row is not None:
                if (
                    row.agent_id != agent_id
                    or row.command_digest != command_digest(command)
                ):
                    raise ConflictError("原启用编号已用于另一份确认内容，请读取原请求")
                receipt = self._receipt(row)
                await self.db.commit()
                return receipt
            await self._agent(project, agent_id)
            row = AgentActivationRequestRecord(
                project_id=project,
                requested_by=actor,
                agent_id=agent_id,
                request_key=command.request_key,
                command_json=command.model_dump_json(),
                command_digest=command_digest(command),
                status="pending",
            )
            self.db.add(row)
            # This DML also establishes a physical transaction before the candidate savepoint.
            await self.db.flush()
            failure: ActivationFailure | None = None
            age = (
                datetime.now(UTC) - command.expected.checked_at
            ).total_seconds()
            if not -30 <= age <= 60:
                failure = "confirmation_expired"
            else:
                try:
                    await AgentService(self.db).update_agent(
                        project,
                        agent_id,
                        AgentUpdate(is_active=True),
                        commit=False,
                        enrich_resources=False,
                        requested_by=actor,
                        expected_activation=command.expected,
                    )
                except ActivationConfirmationConflict:
                    failure = "configuration_changed"
                except AuthorizationError:
                    failure = "permission_changed"
                except ConflictError:
                    failure = "not_ready"
                except ExternalServiceError:
                    failure = "unavailable"
            row.status = "rejected" if failure else "completed"
            row.failure_code = failure
            row.finished_at = datetime.now(UTC)
            await self.db.flush()
            receipt = self._receipt(row)
            await self.db.commit()
            return receipt
        except BaseException:
            await self.db.rollback()
            raise
