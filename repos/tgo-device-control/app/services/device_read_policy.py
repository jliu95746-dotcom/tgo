"""Persist desired read scope and verify each connection's client acknowledgement."""

import asyncio
import hashlib
import ntpath
import posixpath
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_read_policy import DeviceReadPolicy
from app.models.device_scenario import DeviceScenarioDraft
from app.models.device_screen_policy import DeviceScreenPolicy
from app.models.device_script_policy import DeviceScriptPolicy
from app.services.device_script_connection import clear_script_confirmation
from app.schemas.device_read_policy import (
    DeviceReadPolicyPublish,
    DeviceReadPolicyRead,
    DeviceReadPolicyRevoke,
    DeviceReadPolicyTest,
)
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


def read_policy_digest(
    revision: int, directory: str, max_lines: int, max_offset: int
) -> str:
    payload = (
        f"tgo-read-policy-v1\n{revision}\nfs_read\n{directory}\n"
        f"{max_lines}\n{max_offset}\n"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def valid_policy_directory(directory: str) -> bool:
    if not directory or len(directory) > 1024 or any(ord(c) < 32 for c in directory):
        return False
    windows = ntpath.isabs(directory) and bool(ntpath.splitdrive(directory)[0])
    posix = posixpath.isabs(directory)
    if not (windows or posix):
        return False
    separator = "\\" if windows else "/"
    parts = directory.replace("/", separator).replace("\\", separator).split(separator)
    if ".." in parts:
        return False
    path_module = ntpath if windows else posixpath
    normalized = path_module.normpath(directory)
    return path_module.dirname(normalized) != normalized


def allowed_read_arguments(
    directory: str | None,
    max_lines: int | None,
    max_offset: int | None,
    arguments: object,
) -> bool:
    if directory is None or max_lines is None or max_offset is None:
        return False
    if not isinstance(arguments, dict) or set(arguments) - {
        "path",
        "limit",
        "offset",
        "encoding",
    }:
        return False
    path = arguments.get("path")
    limit = arguments.get("limit")
    offset = arguments.get("offset", 0)
    if (
        not isinstance(path, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
    ):
        return False
    if not isinstance(offset, int) or isinstance(offset, bool):
        return False
    if limit < 1 or limit > max_lines or offset < 0 or offset > max_offset:
        return False
    if arguments.get("encoding", "utf-8") != "utf-8":
        return False
    path_module = (
        ntpath
        if ntpath.isabs(directory) and ntpath.splitdrive(directory)[0]
        else posixpath
    )
    if not path_module.isabs(path):
        return False
    try:
        base = path_module.normcase(path_module.normpath(directory))
        candidate = path_module.normcase(path_module.normpath(path))
        return path_module.commonpath([base, candidate]) == base
    except ValueError:
        return False


def set_desired_policy(
    connection: TcpDeviceConnection, policy: DeviceReadPolicy
) -> None:
    clear_script_confirmation(connection)
    connection.desired_read_policy_digest = policy.digest
    connection.applied_read_policy_digest = None
    connection.read_policy_verified_digest = None
    connection.read_policy_directory = policy.allowed_directory
    connection.read_policy_revision = policy.revision
    connection.read_policy_max_lines = policy.max_lines
    connection.read_policy_max_offset = policy.max_offset
    connection.desired_screen_policy = None
    connection.applied_screen_policy_digest = None
    connection.screen_grant = None


def clear_read_confirmation(connection: TcpDeviceConnection) -> None:
    connection.desired_read_policy_digest = None
    connection.applied_read_policy_digest = None
    connection.read_policy_verified_digest = None
    connection.read_policy_directory = None
    connection.read_policy_revision = None
    connection.read_policy_max_lines = None
    connection.read_policy_max_offset = None


async def apply_read_policy(
    connection: TcpDeviceConnection, policy: DeviceReadPolicy
) -> bool:
    if (
        getattr(policy, "enabled", True) is False
        or connection.desired_read_policy_digest != policy.digest
    ):
        return False
    response = await connection.send_request(
        "policy/apply",
        {
            "revision": policy.revision,
            "tool_name": "fs_read",
            "allowed_directory": policy.allowed_directory,
            "max_lines": policy.max_lines,
            "max_offset": policy.max_offset,
            "digest": policy.digest,
        },
        timeout=10,
    )
    if (
        isinstance(response, dict)
        and response.get("revision") == policy.revision
        and response.get("digest") == policy.digest
        and connection.desired_read_policy_digest == policy.digest
    ):
        connection.applied_read_policy_digest = policy.digest
        return True
    return False


class DeviceReadPolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _require_device(
        self, project_id: UUID, device_id: UUID, *, lock: bool = False
    ) -> None:
        query = select(Device.id).where(
            Device.id == device_id, Device.project_id == project_id
        )
        if lock:
            query = query.with_for_update()
        device = await self.db.scalar(query)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

    @staticmethod
    def _response(policy: DeviceReadPolicy) -> DeviceReadPolicyRead:
        connection = tcp_connection_manager.get_connection(str(policy.device_id))
        active = bool(
            policy.enabled is not False
            and connection
            and connection.project_id == str(policy.project_id)
            and connection.desired_read_policy_digest == policy.digest
            and connection.applied_read_policy_digest == policy.digest
        )
        verified = bool(
            active
            and policy.last_test_status == "passed"
            and policy.last_test_digest == policy.digest
            and connection
            and policy.last_test_connection_id
            == getattr(connection, "connection_instance_id", None)
            and connection.read_policy_verified_digest == policy.digest
        )
        last_test_status: Literal["passed", "failed"] | None = None
        if policy.last_test_status == "passed":
            last_test_status = "passed"
        elif policy.last_test_status == "failed":
            last_test_status = "failed"
        return DeviceReadPolicyRead(
            device_id=policy.device_id,
            project_id=policy.project_id,
            revision=policy.revision,
            allowed_directory=policy.allowed_directory,
            max_lines=policy.max_lines,
            max_offset=policy.max_offset,
            digest=policy.digest,
            status="revoked"
            if policy.enabled is False
            else "active"
            if active
            else "pending",
            verified=verified,
            last_tested_at=policy.last_tested_at,
            last_test_status=last_test_status,
            updated_at=policy.updated_at,
            revoked_at=policy.revoked_at,
        )

    async def get(
        self, project_id: UUID, device_id: UUID
    ) -> DeviceReadPolicyRead | None:
        await self._require_device(project_id, device_id)
        policy = await self.db.scalar(
            select(DeviceReadPolicy).where(
                DeviceReadPolicy.device_id == device_id,
                DeviceReadPolicy.project_id == project_id,
            )
        )
        return self._response(policy) if policy else None

    async def publish(
        self, project_id: UUID, device_id: UUID, request: DeviceReadPolicyPublish
    ) -> DeviceReadPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            await self._require_device(project_id, device_id, lock=True)
            draft = await self.db.scalar(
                select(DeviceScenarioDraft)
                .where(
                    DeviceScenarioDraft.device_id == device_id,
                    DeviceScenarioDraft.project_id == project_id,
                    DeviceScenarioDraft.scenario == "files",
                )
                .with_for_update()
            )
            if draft is None or draft.revision != request.expected_draft_revision:
                raise HTTPException(409, "Device scenario draft changed")
            if not draft.target_directory or not valid_policy_directory(
                draft.target_directory
            ):
                raise HTTPException(422, "An absolute, scoped directory is required")
            policy = await self.db.scalar(
                select(DeviceReadPolicy)
                .where(
                    DeviceReadPolicy.device_id == device_id,
                    DeviceReadPolicy.project_id == project_id,
                )
                .with_for_update()
            )
            actual_revision = policy.revision if policy else 0
            if actual_revision != request.expected_policy_revision:
                raise HTTPException(409, "Device read policy changed")
            screen_policy = await self.db.scalar(
                select(DeviceScreenPolicy.device_id).where(
                    DeviceScreenPolicy.device_id == device_id,
                    DeviceScreenPolicy.enabled.is_(True),
                )
            )
            if screen_policy is not None:
                raise HTTPException(
                    409, "Published screen policy conflicts with file mode"
                )
            script_policy = await self.db.scalar(
                select(DeviceScriptPolicy.device_id).where(
                    DeviceScriptPolicy.device_id == device_id,
                    DeviceScriptPolicy.enabled.is_(True),
                )
            )
            if script_policy is not None:
                raise HTTPException(
                    409, "Published script policy conflicts with file mode"
                )
            revision = actual_revision + 1
            digest = read_policy_digest(
                revision, draft.target_directory, request.max_lines, request.max_offset
            )
            if policy is None:
                policy = DeviceReadPolicy(device_id=device_id, project_id=project_id)
                self.db.add(policy)
            policy.revision = revision
            policy.enabled = True
            policy.revoked_at = None
            policy.allowed_directory = draft.target_directory
            policy.max_lines = request.max_lines
            policy.max_offset = request.max_offset
            policy.digest = digest
            policy.last_tested_at = None
            policy.last_test_status = None
            policy.last_test_digest = None
            policy.last_test_connection_id = None
            try:
                await self.db.commit()
            except IntegrityError:
                await self.db.rollback()
                raise HTTPException(409, "Device read policy changed") from None
            await self.db.refresh(policy)
            connection = tcp_connection_manager.get_connection(str(device_id))
            if connection and connection.project_id == str(project_id):
                set_desired_policy(connection, policy)
                await apply_read_policy(connection, policy)
            return self._response(policy)

    async def test_read(
        self, project_id: UUID, device_id: UUID, request: DeviceReadPolicyTest
    ) -> DeviceReadPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            await self._require_device(project_id, device_id)
            policy = await self.db.scalar(
                select(DeviceReadPolicy)
                .where(
                    DeviceReadPolicy.device_id == device_id,
                    DeviceReadPolicy.project_id == project_id,
                )
                .with_for_update()
            )
            if policy is None:
                raise HTTPException(404, "Device read policy not found")
            if policy.enabled is False:
                raise HTTPException(409, "Device read policy is revoked")
            connection = tcp_connection_manager.get_connection(str(device_id))
            if (
                connection is None
                or connection.project_id != str(project_id)
                or connection.desired_read_policy_digest != policy.digest
                or connection.applied_read_policy_digest != policy.digest
            ):
                raise HTTPException(409, "Device read policy is pending")
            arguments = {"path": request.path, "limit": request.limit}
            if not allowed_read_arguments(
                policy.allowed_directory,
                policy.max_lines,
                policy.max_offset,
                arguments,
            ):
                raise HTTPException(422, "Read test is outside the active policy")
            result = await connection.call_tool("fs_read", arguments)
            content = result.get("content") if isinstance(result, dict) else None
            passed = bool(
                isinstance(result, dict)
                and result.get("isError") is False
                and isinstance(content, list)
                and any(
                    isinstance(item, dict)
                    and isinstance(item.get("text"), str)
                    and request.expected_text in item["text"]
                    for item in content
                )
            )
            policy.last_tested_at = datetime.now(timezone.utc)
            policy.last_test_status = "passed" if passed else "failed"
            policy.last_test_digest = policy.digest
            policy.last_test_connection_id = connection.connection_instance_id
            await self.db.commit()
            await self.db.refresh(policy)
            connection.read_policy_verified_digest = policy.digest if passed else None
            return self._response(policy)

    async def revoke(
        self, project_id: UUID, device_id: UUID, request: DeviceReadPolicyRevoke
    ) -> DeviceReadPolicyRead:
        async with tcp_connection_manager.lifecycle_lock(str(device_id)):
            await self._require_device(project_id, device_id, lock=True)
            policy = await self.db.scalar(
                select(DeviceReadPolicy)
                .where(
                    DeviceReadPolicy.device_id == device_id,
                    DeviceReadPolicy.project_id == project_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if policy is None:
                raise HTTPException(404, "Device read policy not found")
            if policy.revision != request.expected_policy_revision:
                raise HTTPException(409, "Device read policy changed")
            connection = tcp_connection_manager.get_connection(str(device_id))
            bound = bool(
                connection
                and connection.project_id == str(project_id)
                and connection.desired_read_policy_digest == policy.digest
            )
            try:
                if policy.enabled is not False:
                    policy.revision += 1
                    policy.enabled = False
                    policy.revoked_at = datetime.now(timezone.utc)
                    policy.digest = read_policy_digest(
                        policy.revision,
                        policy.allowed_directory,
                        policy.max_lines,
                        policy.max_offset,
                    )
                    policy.last_test_status = None
                    policy.last_test_digest = None
                    policy.last_test_connection_id = None
                    if bound and connection:
                        set_desired_policy(connection, policy)
                    await self.db.commit()
                    await self.db.refresh(policy)
            finally:
                if bound and connection:
                    connection.applied_read_policy_digest = None
                    connection.read_policy_verified_digest = None
                    await asyncio.shield(
                        tcp_connection_manager.unregister_connection(
                            str(device_id),
                            expected_connection=connection,
                        )
                    )
            return self._response(policy)
