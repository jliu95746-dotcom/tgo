"""Mount a readonly script with SDK call identity and reviewed values."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NoReturn, cast
from uuid import UUID, uuid5

from agno.tools.function import Function, FunctionCall
from pydantic import JsonValue

from app.models.internal import AgentExecutionContext
from app.schemas.agent_device_script_capability import ScriptRuntimeDefinition
from app.schemas.agent_device_script_runs import ScriptRunView
from app.schemas.device_script_execution import (
    BusinessValue,
    validate_script_values,
)
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.knowledge_evidence import (
    BusinessToolEvidence,
    KnowledgeEvidence,
)
from app.services.agent_execution_scope import (
    AgentExecutionScope,
    ExecutionRevoked,
    current_execution_scope,
)
from app.services.agent_trial_fingerprint import configuration_fingerprint

DefinitionReader = Callable[
    [AgentExecutionContext], Awaitable[ScriptRuntimeDefinition]
]
ScriptRunner = Callable[
    [
        AgentExecutionContext,
        ScriptRuntimeDefinition,
        UUID,
        dict[str, BusinessValue],
    ],
    Awaitable[ScriptRunView],
]
TOOL_NAME = "employee_device_script"


def _deny(scope: AgentExecutionScope | None) -> NoReturn:
    if scope is not None:
        scope.revoke()
    raise ExecutionRevoked()


async def _read_definition(
    context: AgentExecutionContext,
) -> ScriptRuntimeDefinition:
    from app.database import AsyncSessionLocal
    from app.services.agent_device_script_tool_runtime import (
        AgentDeviceScriptToolRuntime,
    )

    return await AgentDeviceScriptToolRuntime(AsyncSessionLocal).definition(
        context
    )


async def _run_script(
    context: AgentExecutionContext,
    definition: ScriptRuntimeDefinition,
    invocation: UUID,
    parameters: dict[str, BusinessValue],
) -> ScriptRunView:
    from app.database import AsyncSessionLocal
    from app.services.agent_device_script_tool_runtime import (
        AgentDeviceScriptToolRuntime,
    )

    return await AgentDeviceScriptToolRuntime(AsyncSessionLocal).execute(
        context,
        definition,
        invocation,
        parameters,
    )


def _parameters(definition: ScriptRuntimeDefinition) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {}
    required: list[JsonValue] = []
    for field in definition.descriptor.parameters:
        prop: dict[str, JsonValue] = {"type": field.kind}
        if field.kind == "string":
            prop["maxLength"] = field.max_length
            if field.choices:
                prop["enum"] = list(field.choices)
        elif field.kind == "integer":
            prop["minimum"], prop["maximum"] = field.minimum, field.maximum
        properties[field.name] = prop
        if field.required:
            required.append(field.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class _Invocation:
    call: FunctionCall
    call_id: str
    identifier: UUID
    values: tuple[tuple[str, BusinessValue], ...]
    owner: asyncio.Task[object]


async def build_device_script_tools(
    context: AgentExecutionContext,
    *,
    definition_reader: DefinitionReader | None = None,
    runner: ScriptRunner | None = None,
) -> list[Function]:
    reference, selected = (
        context.execution_reference,
        context.agent.device_script_selection,
    )
    if (
        context.disable_tools
        or context.response_purpose == "expression"
        or context.knowledge_channel != KnowledgeChannel.INTERNAL
        or reference is None
        or reference.channel != KnowledgeChannel.INTERNAL
        or selected is None
        or not selected.enabled
    ):
        return []
    scope = current_execution_scope()
    if (
        scope is None
        or scope.reference != reference
        or selected.project_id != reference.project_id
        or selected.agent_id != reference.agent_id
        or context.project_id != str(reference.project_id)
        or configuration_fingerprint(context.agent)
        != reference.configuration_fingerprint
        or reference.business_evidence_revision is None
    ):
        _deny(scope)
    try:
        await scope.check()
        definition = await (definition_reader or _read_definition)(context)
        definition = ScriptRuntimeDefinition.model_validate_json(
            definition.model_dump_json()
        )
        now = datetime.now(timezone.utc)
        if (
            definition.selection != selected
            or definition.remaining_runs < 1
            or not -5 <= (now - definition.checked_at).total_seconds() <= 30
            or definition.valid_until <= now
        ):
            _deny(scope)
        await scope.check()
    except Exception:
        _deny(scope)
    execute = runner or _run_script
    serial = asyncio.Lock()
    invocation: ContextVar[_Invocation | None] = ContextVar(
        "employee_script_sdk_invocation", default=None
    )

    def owns_call(call: FunctionCall) -> bool:
        # Agno copies Function models, retaining the same server closure.
        return (
            call.function.name == TOOL_NAME
            and call.function.entrypoint is entrypoint
        )

    async def before(fc: FunctionCall) -> None:
        invocation.set(None)
        try:
            call_id = fc.call_id
            if (
                not owns_call(fc)
                or not isinstance(call_id, str)
                or not 1 <= len(call_id) <= 256
            ):
                _deny(scope)
            values = validate_script_values(
                definition.descriptor.parameters,
                fc.arguments or {},
                input=True,
            )
            identifier = uuid5(
                context.execution_id,
                f"employee-script-sdk-v1:{selected.id}:{call_id}",
            )
            await scope.check()
            invocation.set(
                _Invocation(
                    fc,
                    call_id,
                    identifier,
                    tuple(sorted(values.items())),
                    cast(asyncio.Task[object], asyncio.current_task()),
                )
            )
        except Exception:
            _deny(scope)

    def after() -> None:
        invocation.set(None)

    async def invoke(parameters: dict[str, BusinessValue]) -> str:
        try:
            current = invocation.get()
            values = validate_script_values(
                definition.descriptor.parameters, parameters, input=True
            )
            if (
                current is None
                or current.owner is not asyncio.current_task()
                or current_execution_scope() is not scope
                or context.execution_reference != reference
                or context.agent.device_script_selection != selected
                or not owns_call(current.call)
                or current.call.call_id != current.call_id
                or tuple(sorted(values.items())) != current.values
            ):
                _deny(scope)
            await scope.check()
            result = await execute(
                context, definition, current.identifier, values
            )
            result = ScriptRunView.model_validate_json(
                result.model_dump_json()
            )
            expected_request = uuid5(
                context.execution_id,
                f"employee-device-script-v1:{current.identifier}",
            )
            if (
                result.project_id != selected.project_id
                or result.actor_id != selected.configured_by
                or result.agent_id != selected.agent_id
                or result.report_id != selected.report_id
                or result.review_id != selected.review_id
                or result.request_id != expected_request
            ):
                _deny(scope)
            await scope.check()
            passed = (
                result.status == "succeeded"
                and result.result_fields is not None
            )
            content = (
                json.dumps(
                    validate_script_values(
                        definition.descriptor.result_fields,
                        result.result_fields,
                        input=False,
                    ),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if passed and result.result_fields is not None
                else (
                    "业务执行结果尚未确认，请查询原运行记录。"
                    if result.status in {"unknown", "preparing"}
                    else "业务脚本未成功执行，无法确认结果，请核对配置或转人工。"
                )
            )
            if context.knowledge_evidence is None:
                context.knowledge_evidence = KnowledgeEvidence(
                    status="skipped",
                    retrieved_at=datetime.now(timezone.utc),
                    project_id=context.project_id,
                    channel=KnowledgeChannel.INTERNAL.value,
                )
            context.knowledge_evidence.tool_results.append(
                BusinessToolEvidence(
                    name=TOOL_NAME,
                    content=content,
                    success=passed,
                    query_id=result.request_id,
                )
            )
            return content
        except Exception:
            _deny(scope)
        finally:
            invocation.set(None)

    async def entrypoint(**parameters: BusinessValue) -> str:
        async with serial:
            return await invoke(parameters)

    return [
        Function(
            name=TOOL_NAME,
            description="运行已验证的只读业务脚本：" + definition.descriptor.title,
            parameters=_parameters(definition),
            entrypoint=entrypoint,
            skip_entrypoint_processing=True,
            pre_hook=before,
            post_hook=after,
            cache_results=False,
            show_result=False,
        )
    ]
