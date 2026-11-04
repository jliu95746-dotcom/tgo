"""Dispatch one reserved readonly execution; duplicate requests only query."""

import asyncio
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.device_script_execution import DeviceScriptExecution
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_script_execution import (
    ScriptExecuteBody,
    ScriptExecuteRequest,
    ScriptReceiptQuery,
    ScriptWireReceipt,
)
from app.schemas.device_script_receipt import ScriptExecutionRead
from app.services.device_script_cancellation import (
    cancel_script_execution,
    close_script_scope,
    require_script_cancellation,
)
from app.services.device_script_connection import (
    connection_is_current,
    reviewed_script,
)
from app.services.device_script_execution_store import (
    ScriptExecutionStore,
    require_script_execution_scope,
)
from app.services.device_script_receipt_store import ScriptReceiptStore
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)

logger = get_logger(__name__)


class ScriptExecutionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.receipts = ScriptReceiptStore(db)

    @staticmethod
    def _ready(
        principal: DeviceServicePrincipal, device: UUID
    ) -> TcpDeviceConnection:
        connection = tcp_connection_manager.get_connection(str(device))
        if (
            connection is None
            or not connection_is_current(connection)
            or connection.project_id != str(principal.project_id)
            or not connection.script_policy_required
            or connection.desired_script_policy is None
            or connection.desired_script_policy.digest
            != principal.script_policy_digest
            or connection.applied_script_policy_ack is None
            or connection.applied_script_policy_ack.digest
            != principal.script_policy_digest
            or connection.applied_script_policy_ack.session_id
            != connection.script_session_id
            or connection.desired_read_policy_digest is not None
            or connection.desired_screen_policy is not None
        ):
            raise HTTPException(
                409, "Bound script connection is not confirmed"
            )
        return connection

    @staticmethod
    def _query(
        connection: TcpDeviceConnection, row: DeviceScriptExecution
    ) -> ScriptReceiptQuery:
        return ScriptReceiptQuery(
            protocol_version=1,
            session_id=connection.script_session_id or "",
            execution_id=str(row.execution_id),
            policy_digest=row.policy_digest,
            parameters_digest=row.parameters_digest,
            context_digest=row.context_digest,
        )

    async def _observe(
        self,
        principal: DeviceServicePrincipal,
        connection: TcpDeviceConnection,
        row: DeviceScriptExecution,
        raw: object,
        query: ScriptReceiptQuery,
    ) -> ScriptExecutionRead:
        device, execution = row.device_id, row.execution_id
        try:
            wire = ScriptWireReceipt.model_validate(raw)
            item = (
                await reviewed_script(connection, row.script_id)
                if wire.status == "succeeded"
                and await self.receipts.active(principal, row)
                else None
            )
            return await self.receipts.finalize(
                principal, row.device_id, row.execution_id, wire, item
            )
        except (ValidationError, ValueError, HTTPException) as error:
            if isinstance(error, HTTPException) and error.status_code in {
                403,
                404,
            }:
                raise
            await self.db.rollback()
            scope_changed = (
                isinstance(error, HTTPException) and error.status_code == 409
            )
            if not scope_changed:
                await close_script_scope(connection, query)
            saved = await self.receipts.owned(principal, device, execution)
            if saved.status == "unknown":
                reason = (
                    "receipt_unavailable" if raw is None else "invalid_receipt"
                )
                if scope_changed:
                    reason = "receipt_scope_changed"
                saved.failure = reason
                await self.db.commit()
                logger.warning(
                    "Script receipt unavailable: execution=%s reason=%s",
                    execution,
                    reason,
                )
            return self.receipts.metadata(saved)

    async def read(
        self, principal: DeviceServicePrincipal, device: UUID, execution: UUID
    ) -> ScriptExecutionRead:
        row = await self.receipts.owned(principal, device, execution)
        metadata = self.receipts.metadata(row)
        if (
            not settings.SCRIPT_BUSINESS_EXECUTION_ENABLED
            or not await self.receipts.active(principal, row)
        ):
            return metadata
        await self.db.commit()
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            try:
                connection = self._ready(principal, device)
            except HTTPException:
                return metadata
            raw = await connection.send_request(
                "scripts/receipt/get",
                (query := self._query(connection, row)).model_dump(),
                timeout=10,
            )
        return await self._observe(principal, connection, row, raw, query)

    async def execute(
        self,
        principal: DeviceServicePrincipal,
        device: UUID,
        execution: UUID,
        body: ScriptExecuteBody,
    ) -> ScriptExecutionRead:
        require_script_execution_scope(principal, device)
        if not settings.SCRIPT_BUSINESS_EXECUTION_ENABLED:
            raise HTTPException(409, "Script business execution is disabled")
        try:
            await self.receipts.owned(principal, device, execution)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        else:
            await ScriptExecutionStore(self.db).reserve(
                principal, device, execution, body.parameters, None
            )
            return await self.read(principal, device, execution)
        await self.db.commit()
        async with tcp_connection_manager.lifecycle_lock(str(device)):
            connection = self._ready(principal, device)
            policy = connection.desired_script_policy
            if policy is None:
                raise HTTPException(
                    409, "Bound script policy is not confirmed"
                )
            item = await reviewed_script(connection, policy.script_id)
            await require_script_cancellation(connection)
            row, fresh = await ScriptExecutionStore(self.db).reserve(
                principal, device, execution, body.parameters, item
            )
            query = self._query(connection, row)
            request = ScriptExecuteRequest(
                **query.model_dump(),
                timeout_seconds=policy.timeout_seconds,
                parameters=body.parameters,
            )
        if not fresh:
            return await self.read(principal, device, execution)
        # The durable record is committed before this first possible dispatch.
        operation = asyncio.create_task(
            connection.send_request(
                "scripts/run",
                request.model_dump(),
                timeout=request.timeout_seconds + 10,
            )
        )
        try:
            while not operation.done():
                await asyncio.wait({operation}, timeout=1)
                if operation.done():
                    break
                active = await self.receipts.active(principal, row)
                await self.db.commit()
                if not active or not connection_is_current(connection):
                    await cancel_script_execution(connection, query)
                    break
            raw = await operation
            return await self._observe(principal, connection, row, raw, query)
        except BaseException:
            await asyncio.shield(cancel_script_execution(connection, query))
            raise
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
