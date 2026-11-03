"""Stop an exact script execution without disrupting newer device work."""

from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.device_script_execution import (
    ScriptCancellationAck,
    ScriptReceiptQuery,
)
from app.services.device_script_connection import connection_is_current
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
)
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


async def _cancellation_ack(
    connection: TcpDeviceConnection, query: ScriptReceiptQuery
) -> ScriptCancellationAck | None:
    raw = await connection.send_request(
        "scripts/cancel", query.model_dump(), timeout=5
    )
    try:
        ack = ScriptCancellationAck.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        return None
    if ack.model_dump(exclude={"signaled"}) != query.model_dump():
        return None
    return ack


async def require_script_cancellation(connection: TcpDeviceConnection) -> None:
    policy = connection.desired_script_policy
    if policy is None or connection.script_session_id is None:
        raise HTTPException(409, "Bound script connection is not confirmed")
    # A fresh, non-business identity checks old-client compatibility before any
    # durable reservation. This never claims a run or consumes its quota.
    query = ScriptReceiptQuery(
        protocol_version=1,
        session_id=connection.script_session_id,
        execution_id=str(uuid4()),
        policy_digest=policy.digest,
        parameters_digest="0" * 64,
        context_digest="0" * 64,
    )
    ack = await _cancellation_ack(connection, query)
    if (
        ack is None
        or ack.signaled
        or not connection_is_current(connection)
        or connection.desired_script_policy != policy
        or connection.script_session_id != query.session_id
    ):
        raise HTTPException(409, "Device does not confirm scoped cancellation")


async def close_script_scope(
    connection: TcpDeviceConnection, query: ScriptReceiptQuery
) -> None:
    async with tcp_connection_manager.lifecycle_lock(connection.agent_id):
        if (
            connection_is_current(connection)
            and connection.desired_script_policy is not None
            and connection.desired_script_policy.digest == query.policy_digest
            and connection.script_session_id == query.session_id
        ):
            await DeviceScriptPublicationService._close(connection)


async def cancel_script_execution(
    connection: TcpDeviceConnection, query: ScriptReceiptQuery
) -> None:
    ack = await _cancellation_ack(connection, query)
    # False means no active process needed signaling; a matching client also
    # prevents this exact queued identity from starting after cancellation.
    if ack is not None:
        return
    # An unconfirmed cancel fails closed only for the original physical scope.
    await close_script_scope(connection, query)
