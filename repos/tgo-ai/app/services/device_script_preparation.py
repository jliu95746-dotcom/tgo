"""Pure immutable preparation shared by tracked runtime and durable trials."""

import hashlib
from collections.abc import Mapping
from uuid import UUID

from app.schemas.device_script_execution import (
    PreparedScriptCall,
    ScriptExecuteBody,
    ScriptPolicyRead,
    script_input_digest,
    validate_reviewed_script_configuration,
    validate_script_values,
)
from app.schemas.device_script_policy import ScriptDescriptor
from app.schemas.device_session import DeviceExecutionIdentity


def prepare_script_call(
    identity: DeviceExecutionIdentity,
    bound: ScriptPolicyRead,
    descriptor: ScriptDescriptor,
    parameters: Mapping[str, object],
    execution_id: UUID,
) -> PreparedScriptCall:
    owned, item = validate_reviewed_script_configuration(
        identity.project_id, identity.device_id, bound, descriptor
    )
    policy = owned.policy
    values = validate_script_values(item.parameters, parameters)
    context = (
        f"tgo-approved-script-context-v1\n{identity.project_id}\n"
        f"{identity.device_id}\n{identity.session_id}\n{identity.agent_id}\n"
        f"{identity.workflow_execution_id or ''}\n{policy.digest}\n"
    )
    return PreparedScriptCall(
        identity=identity,
        execution_id=execution_id,
        policy_digest=policy.digest,
        parameters_digest=script_input_digest(policy.grant_digest, values),
        context_digest=hashlib.sha256(context.encode()).hexdigest(),
        timeout_seconds=policy.timeout_seconds,
        max_output_bytes=policy.max_output_bytes,
        parameters_json=ScriptExecuteBody(parameters=values).model_dump_json(),
        descriptor_json=item.model_dump_json(),
    )
