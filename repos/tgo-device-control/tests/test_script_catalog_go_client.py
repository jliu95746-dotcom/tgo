"""Opt-in Go binary/TCP contract with isolated database ownership checks."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.device_script_policy import (
    ScriptPolicy,
    ScriptPolicyAck,
    ScriptPolicyApply,
    ScriptSessionStatus,
    script_policy_digest,
)
from app.services import device_script_catalog as module
from app.services.tcp_connection_manager import TcpDeviceConnection


@pytest.mark.asyncio
@pytest.mark.parametrize("script_enabled", [False, True])
async def test_reviewed_catalog_with_real_go_client(
    tmp_path, monkeypatch, script_enabled
):
    executable = os.environ.get("TGO_SCRIPT_CATALOG_TEST_AGENT")
    if not executable:
        pytest.skip(
            "set TGO_SCRIPT_CATALOG_TEST_AGENT to a freshly built agent"
        )
    if script_enabled and sys.platform != "win32":
        pytest.skip("protected local script sessions require Windows DPAPI")
    agent = Path(executable).resolve(strict=True)
    project, device, bind_code = uuid4(), uuid4(), uuid4().hex
    script, runtime = tmp_path / "daily.py", Path(sys.executable)
    marker = tmp_path / "script-ran"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "print('{\"ready\": true}')\n",
        encoding="utf-8",
    )
    ready = asyncio.get_running_loop().create_future()
    stopping = asyncio.Event()
    handlers = []

    async def accept(reader, writer):
        handlers.append(asyncio.current_task())
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), 10))
            assert request["method"] == "auth"
            assert request["params"]["bindCode"] == bind_code
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "status": "ok",
                    "deviceId": str(device),
                    "projectId": str(project),
                    "deviceToken": uuid4().hex,
                },
            }
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
            connection = TcpDeviceConnection(
                agent_id=str(device),
                name="owned-go-catalog-test",
                version="test",
                capabilities=[],
                reader=reader,
                writer=writer,
                project_id=str(project),
                device_db_id=str(device),
            )
            ready.set_result(connection)
            while line := await reader.readline():
                connection.handle_response(json.loads(line))
        except BaseException as exc:
            if not (stopping.is_set() and isinstance(exc, ConnectionError)):
                if not ready.done():
                    ready.set_exception(exc)
                raise
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                if not stopping.is_set():
                    raise

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    config = tmp_path / "agent.json"
    config.write_text(
        json.dumps(
            {
                "server_host": "127.0.0.1",
                "server_port": port,
                "bind_code": bind_code,
                "token_file": str(tmp_path / "token"),
                "work_root": str(tmp_path),
                "log_level": "error",
                "max_reconnect_attempts": 1,
                "script_execution_enabled": script_enabled,
                "script_state_directory": str(tmp_path / "private"),
                "approved_scripts": [
                    {
                        "approved": True,
                        "script_id": "daily_report",
                        "title": "每日业务报表",
                        "version": "v1",
                        "runner": "python",
                        "script_path": str(script),
                        "content_sha256": hashlib.sha256(
                            script.read_bytes()
                        ).hexdigest(),
                        "interpreter": str(runtime),
                        "interpreter_sha256": hashlib.sha256(
                            runtime.read_bytes()
                        ).hexdigest(),
                        "working_directory": str(tmp_path),
                        "effect": "read_only",
                        "parameters": [],
                        "arguments": [],
                        "result_fields": [
                            {
                                "name": "ready",
                                "kind": "boolean",
                                "required": True,
                            }
                        ],
                        "timeout_seconds": 10,
                        "max_output_bytes": 4096,
                        "max_runs": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(agent),
            "--config",
            str(config),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        connection = await asyncio.wait_for(ready, 15)
        monkeypatch.setattr(
            module.tcp_connection_manager,
            "get_connection",
            lambda _: connection,
        )
        db = AsyncMock(spec=AsyncSession)
        db.scalar.return_value = device
        service = module.DeviceScriptCatalogService(db)
        catalog = await service.get_catalog(project, device)
        assert catalog.scripts[0].title == "每日业务报表"
        assert catalog.scripts[0].parameters == []
        assert str(tmp_path) not in catalog.model_dump_json()
        assert not marker.exists()
        status = ScriptSessionStatus.model_validate(
            await connection.send_request("scripts/status", {}, timeout=10)
        )
        policy_request = None
        if script_enabled:
            assert status.state == "awaiting_policy"
            assert status.session_id is not None
            item = catalog.scripts[0]
            values = dict(
                protocol_version=1,
                revision=1,
                script_id=item.script_id,
                grant_digest=item.grant_digest,
                timeout_seconds=item.timeout_seconds,
                max_output_bytes=item.max_output_bytes,
                max_runs=item.max_runs,
            )
            # Compute the wire digest before validation of the complete policy.
            draft = ScriptPolicy.model_construct(**values, digest="")
            policy = ScriptPolicy.model_validate(
                {**values, "digest": script_policy_digest(draft)}
            )
            policy_request = ScriptPolicyApply(
                session_id=status.session_id, policy=policy
            )
            ack = ScriptPolicyAck.model_validate(
                await connection.send_request(
                    "scripts/policy/apply",
                    policy_request.model_dump(),
                    timeout=10,
                )
            )
            assert ack.session_id == status.session_id
            assert ack.digest == policy.digest
            assert ack.revision == policy.revision
            active = ScriptSessionStatus.model_validate(
                await connection.send_request("scripts/status", {}, timeout=10)
            )
            assert active.state == "active"
            assert active.session_id == status.session_id
            assert not marker.exists()
        else:
            assert status.state == "disabled"
            assert not (tmp_path / "private").exists()
        script.write_text("changed after local review", encoding="utf-8")
        with pytest.raises(HTTPException) as error:
            await service.get_catalog(project, device)
        assert error.value.status_code == 409
        if policy_request is not None:
            rejected = await connection.send_request(
                "scripts/policy/apply", policy_request.model_dump(), timeout=10
            )
            assert "error" in rejected
            inactive = ScriptSessionStatus.model_validate(
                await connection.send_request("scripts/status", {}, timeout=10)
            )
            assert inactive.state == "awaiting_policy"
            assert not marker.exists()
    finally:
        stopping.set()
        try:
            if process is not None and process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 10)
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.wait_for(asyncio.gather(*handlers), 10)
        finally:
            config.unlink(missing_ok=True)
            (tmp_path / "token").unlink(missing_ok=True)
