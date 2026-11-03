"""Connection-local script confirmation without business execution."""

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.device_script_policy import (
    ScriptCatalog,
    ScriptDescriptor,
    ScriptPolicy,
    ScriptPolicyAck,
    ScriptPolicyApply,
    ScriptSessionStatus,
)
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


def clear_script_confirmation(
    connection: TcpDeviceConnection, *, required: bool = False
) -> None:
    connection.script_policy_required = required
    connection.desired_script_policy = None
    connection.script_session_id = None
    connection.applied_script_policy_ack = None


def validate_script_scope(
    item: ScriptDescriptor, policy: ScriptPolicy
) -> bool:
    return bool(
        item.effect == "read_only"
        and item.script_id == policy.script_id
        and item.grant_digest == policy.grant_digest
        and policy.timeout_seconds <= item.timeout_seconds
        and policy.max_output_bytes <= item.max_output_bytes
        and policy.max_runs <= item.max_runs
    )


def connection_is_current(connection: TcpDeviceConnection) -> bool:
    return bool(
        tcp_connection_manager.get_connection(connection.agent_id)
        is connection
        and connection.agent_id == connection.device_db_id
        and not connection.writer.is_closing()
    )


async def script_status(
    connection: TcpDeviceConnection,
) -> ScriptSessionStatus:
    raw = await connection.send_request("scripts/status", {}, timeout=10)
    if not connection_is_current(connection):
        raise HTTPException(409, "Device connection changed")
    try:
        return ScriptSessionStatus.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        connection.applied_script_policy_ack = None
        raise HTTPException(
            409, "Local script session is unavailable"
        ) from None


async def reviewed_script(
    connection: TcpDeviceConnection, script_id: str
) -> ScriptDescriptor:
    raw = await connection.send_request("scripts/list", {}, timeout=10)
    if not connection_is_current(connection):
        raise HTTPException(409, "Device connection changed")
    try:
        catalog = ScriptCatalog.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(
            409, "Reviewed script catalog is unavailable"
        ) from None
    item = next(
        (item for item in catalog.scripts if item.script_id == script_id), None
    )
    if item is None:
        raise HTTPException(409, "Reviewed script is unavailable")
    return item


async def apply_script_policy(
    connection: TcpDeviceConnection, policy: ScriptPolicy
) -> bool:
    connection.script_policy_required = True
    connection.desired_script_policy = policy
    connection.applied_script_policy_ack = None
    try:
        status = await script_status(connection)
        if (
            status.state not in {"awaiting_policy", "active"}
            or status.session_id is None
        ):
            return False
        item = await reviewed_script(connection, policy.script_id)
        if not validate_script_scope(item, policy):
            return False
        session_id = status.session_id
        connection.script_session_id = session_id
        request = ScriptPolicyApply(session_id=session_id, policy=policy)
        raw = await connection.send_request(
            "scripts/policy/apply", request.model_dump(), timeout=10
        )
        ack = ScriptPolicyAck.model_validate(raw)
        status = await script_status(connection)
        if (
            connection_is_current(connection)
            and connection.desired_script_policy == policy
            and connection.script_session_id == session_id
            and ack.session_id == session_id
            and ack.revision == policy.revision
            and ack.digest == policy.digest
            and status.state == "active"
            and status.session_id == session_id
        ):
            connection.applied_script_policy_ack = ack
            return True
    except (HTTPException, ValidationError, ValueError, TypeError):
        pass
    connection.applied_script_policy_ack = None
    return False
