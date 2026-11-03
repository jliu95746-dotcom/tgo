"""Prepared readonly calls belong to an existing renewable AI execution."""

import asyncio
from collections.abc import Mapping
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.exceptions import ExternalServiceError
from app.schemas.device_script_execution import (
    PreparedScriptCall,
    ScriptExecutionRead,
    ScriptPolicyRead,
)
from app.schemas.device_script_policy import ScriptDescriptor
from app.services.device_control_client import device_control_client
from app.services.device_script_preparation import prepare_script_call
from app.services.device_execution import (
    DeviceExecution,
    current_device_execution,
)


class ApprovedScriptExecution:
    """One POST attempt; later observations always query the original ID."""

    def __init__(
        self, call: PreparedScriptCall, execution: DeviceExecution
    ) -> None:
        self.call = call
        self._execution = execution
        self._attempted = False
        self._lock = asyncio.Lock()

    @classmethod
    def prepare(
        cls,
        bound: ScriptPolicyRead,
        descriptor: ScriptDescriptor,
        parameters: Mapping[str, object],
        *,
        execution_id: UUID | None = None,
    ) -> "ApprovedScriptExecution":
        execution = current_device_execution()
        if execution is None or execution.closed or execution.heartbeat_failed:
            raise ExternalServiceError("device-control", "只读脚本需要有效的设备执行会话")
        identity = execution.identity
        try:
            call = prepare_script_call(
                identity, bound, descriptor, parameters,
                (
                    UUID(str(execution_id))
                    if execution_id is not None else uuid4()
                ),
            )
        except (ValidationError, ValueError, TypeError):
            raise ExternalServiceError(
                "device-control", "脚本范围或业务输入无效，请重新核对配置"
            ) from None
        return cls(call, execution)

    def _require_active(self) -> None:
        if (
            current_device_execution() is not self._execution
            or self._execution.closed
            or self._execution.heartbeat_failed
            or self._execution.identity != self.call.identity
        ):
            raise ExternalServiceError("device-control", "设备执行会话已经结束或发生变化")

    async def run(self) -> ScriptExecutionRead:
        async with self._lock:
            self._require_active()
            if self._attempted:
                result = await device_control_client.read_script(self.call)
            else:
                # Set before I/O: cancellation cannot authorize replay.
                self._attempted = True
                result = await device_control_client.execute_script(self.call)
            self._require_active()
            return result

    async def read(self) -> ScriptExecutionRead:
        async with self._lock:
            self._require_active()
            self._attempted = True
            result = await device_control_client.read_script(self.call)
            self._require_active()
            return result
