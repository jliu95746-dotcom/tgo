"""Reconfirm script scope after authentication with a fresh acknowledgement."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_read_policy import DeviceReadPolicy
from app.models.device_screen_policy import DeviceScreenPolicy
from app.models.device_script_policy import DeviceScriptPolicy
from app.services.device_read_policy import clear_read_confirmation
from app.services.device_script_connection import (
    apply_script_policy,
    clear_script_confirmation,
)
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
    stored_script_policy,
)
from app.services.tcp_connection_manager import TcpDeviceConnection


async def restore_script_policy(
    db: AsyncSession,
    connection: TcpDeviceConnection,
) -> bool:
    """Return true when script history owns the mode, including revocation."""
    project, device = UUID(connection.project_id or ""), UUID(
        connection.agent_id
    )
    row = await db.get(DeviceScriptPolicy, device)
    if row is None:
        return False
    try:
        owned = await db.scalar(
            select(Device.id)
            .where(
                Device.id == device,
                Device.project_id == project,
            )
            .with_for_update()
        )
        if owned is None or row.project_id != project:
            raise ValueError("Script policy ownership changed")
        # Reload after acquiring the cross-process device lock.
        await db.refresh(row)
        read_policy = await db.get(DeviceReadPolicy, device)
        screen_policy = await db.get(DeviceScreenPolicy, device)
        other_active = bool(
            read_policy
            and read_policy.enabled
            or screen_policy
            and screen_policy.enabled
        )
        if not row.enabled and other_active:
            clear_script_confirmation(connection)
            return False
        if other_active:
            raise ValueError("Conflicting device policy modes")
        clear_read_confirmation(connection)
        connection.desired_screen_policy = None
        connection.applied_screen_policy_digest = None
        connection.screen_grant = None
        clear_script_confirmation(connection, required=True)
        if row.enabled:
            if not await apply_script_policy(
                connection, stored_script_policy(row)
            ):
                await DeviceScriptPublicationService._close(connection)
        await db.commit()
    except BaseException:
        await DeviceScriptPublicationService._close(connection)
        raise
    return True
