"""Real Go auth/TCP, signed HTTP and PostgreSQL policies on loopback."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from jose import jwt

from app.config import settings
from app.core.database import get_async_db
from app.main import app
from app.models.device import Device
from app.models.device_script_policy import DeviceScriptPolicy
from app.services import tcp_rpc_server as server_module
from app.services.mcp_server import MCPProxy
from app.services.tcp_connection_manager import TcpConnectionManager
import test_script_publication_database as database_tests
from script_execution_runtime_scenarios import (
    assert_receipt,
    exercise_execution,
    heartbeat,
)
from script_ai_runtime import exercise_ai_client

pytestmark = database_tests.pytestmark
script_database = database_tests.script_database


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "configure",
        "execute",
        "cancel",
        "abort",
        "ai-client",
        "ai-trial",
        "ai-trial-cancel",
        "ai-case-trial",
    ],
)
async def test_real_agent_persistent_policy_reconnect_and_revoke(
    script_database,
    tmp_path,
    monkeypatch,
    scenario,
):
    executable = os.environ.get("TGO_SCRIPT_CATALOG_TEST_AGENT")
    if not executable or sys.platform != "win32":
        pytest.skip("requires an explicit current Windows Go agent binary")
    agent = Path(executable).resolve(strict=True)
    sessions, project, device, _, _, _, _ = script_database
    manager = server_module.tcp_connection_manager
    monkeypatch.setattr(
        manager,
        "get_connection",
        lambda value: TcpConnectionManager.get_connection(manager, value),
    )

    async def unregister(agent_id, expected_connection=None):
        return await TcpConnectionManager.unregister_connection(
            manager, agent_id, expected_connection
        )

    monkeypatch.setattr(manager, "unregister_connection", unregister)
    monkeypatch.setattr(server_module, "AsyncSessionLocal", sessions)
    device_token = str(uuid4())
    async with sessions() as db:
        saved_device = await db.get(Device, device)
        saved_device.device_token = device_token
        await db.commit()
    server = server_module.TcpRpcServer("127.0.0.1", 0)
    await server.start()
    port = server.server.sockets[0].getsockname()[1]
    source = tmp_path / "daily.py"
    marker = tmp_path / "unexpected-execution"
    progress = tmp_path / "running-progress"
    code = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "print('{\"ready\":true}')\n"
    )
    if scenario != "configure":
        code = (
            "from pathlib import Path\nimport sys,json,time\n"
            f"p=Path({str(marker)!r})\n"
            "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
        )
        if scenario in {"cancel", "abort", "ai-trial-cancel"}:
            code += (
                f"q=Path({str(progress)!r})\nfor i in range(100):\n"
                " q.write_text(str(i))\n time.sleep(.2)\n"
            )
        code += "print(json.dumps({'ready':True,'label':sys.argv[1]}))\n"
    source.write_text(code, encoding="utf-8")
    runtime = Path(sys.executable)
    config = tmp_path / "agent.json"
    config.write_text(
        json.dumps(
            dict(
                server_host="127.0.0.1",
                server_port=port,
                device_token=device_token,
                token_file=str(tmp_path / "token"),
                work_root=str(tmp_path),
                device_name="隔离脚本客户端",
                log_level="error",
                max_reconnect_attempts=3,
                script_execution_enabled=True,
                script_state_directory=str(tmp_path / "private"),
                approved_scripts=[
                    dict(
                        approved=True,
                        script_id="daily_report",
                        title="隔离脚本策略验收",
                        version="v1",
                        runner="python",
                        script_path=str(source),
                        content_sha256=hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                        interpreter=str(runtime),
                        interpreter_sha256=hashlib.sha256(
                            runtime.read_bytes()
                        ).hexdigest(),
                        working_directory=str(tmp_path),
                        effect="read_only",
                        parameters=[]
                        if scenario == "configure"
                        else [
                            dict(
                                name="label",
                                kind="string",
                                required=True,
                                max_length=80,
                            )
                        ],
                        arguments=[]
                        if scenario == "configure"
                        else [dict(parameter="label")],
                        result_fields=[
                            dict(name="ready", kind="boolean", required=True)
                        ]
                        + (
                            []
                            if scenario == "configure"
                            else [
                                dict(
                                    name="label",
                                    kind="string",
                                    required=True,
                                    max_length=80,
                                )
                            ]
                        ),
                        timeout_seconds=10,
                        max_output_bytes=4096,
                        max_runs=1,
                    )
                ],
            )
        ),
        encoding="utf-8",
    )
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    now = datetime.now(timezone.utc)
    headers = {
        "Authorization": "Bearer "
        + jwt.encode(
            dict(
                sub="tgo-api",
                project_id=str(project),
                device_id=str(device),
                iat=now,
                exp=now + timedelta(minutes=3),
                aud="tgo-device-control",
                iss="tgo-internal",
            ),
            key,
            algorithm="HS256",
        )
    }

    async def database():
        async with sessions() as db:
            yield db

    previous = app.dependency_overrides.get(get_async_db)
    app.dependency_overrides[get_async_db] = database
    process = None

    async def start():
        return await asyncio.create_subprocess_exec(
            str(agent),
            "--config",
            str(config),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    async def wait_connection(*, acknowledged=False, different=None):
        async with asyncio.timeout(30):
            while True:
                connection = manager.get_connection(str(device))
                if (
                    connection
                    and connection is not different
                    and (
                        not acknowledged
                        or connection.applied_script_policy_ack is not None
                    )
                ):
                    return connection
                if process.returncode is not None:
                    raise AssertionError(
                        f"Owned Agent exited: {process.returncode}"
                    )
                await asyncio.sleep(0.02)

    try:
        process = await start()
        await wait_connection()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as client:
            path = f"/v1/devices/{device}"
            status = await client.get(path + "/script-status")
            assert status.status_code == 200, status.text
            initial = status.json()
            assert initial["session"]["state"] == "awaiting_policy"
            catalog = await client.get(path + "/script-catalog")
            assert catalog.status_code == 200, catalog.text
            grant = catalog.json()["scripts"][0]
            response = await client.put(
                path + "/script-policy",
                json=dict(
                    expected_policy_revision=0,
                    connection_instance_id=initial["connection_instance_id"],
                    session_id=initial["session"]["session_id"],
                    script_id=grant["script_id"],
                    grant_digest=grant["grant_digest"],
                    timeout_seconds=10,
                    max_output_bytes=4096,
                    max_runs=1,
                ),
            )
            assert response.status_code == 200, response.text
            assert (
                response.json()["status"] == "active"
                and not response.json()["verified"]
            )
            first = await wait_connection(acknowledged=True)
            nonce = first.applied_script_policy_ack.session_id
            if scenario in {
                "ai-client",
                "ai-trial",
                "ai-trial-cancel",
                "ai-case-trial",
            }:
                await exercise_ai_client(
                    sessions,
                    response.json(),
                    grant,
                    marker,
                    tmp_path,
                    monkeypatch,
                    key,
                    trial=scenario
                    in {"ai-trial", "ai-trial-cancel", "ai-case-trial"},
                    saved_case=scenario == "ai-case-trial",
                    cancel_progress=(
                        progress if scenario == "ai-trial-cancel" else None
                    ),
                )
                assert process.returncode is None
                assert manager.get_connection(str(device)) is first
                return
            execution_state = None
            if scenario != "configure":
                execution_state = await exercise_execution(
                    client,
                    sessions,
                    project,
                    device,
                    key,
                    response.json()["policy"]["digest"],
                    marker,
                    progress,
                    monkeypatch,
                    scenario,
                )
                if scenario in {"cancel", "abort"}:
                    assert process.returncode is None
                    revoked = await client.post(
                        path + "/script-policy/revoke",
                        json=dict(expected_policy_revision=1),
                    )
                    assert revoked.status_code == 200, revoked.text
                    return
                await heartbeat(client, device, execution_state)
            source.write_text("changed after local review", encoding="utf-8")
            response = await client.get(path + "/script-policy")
            assert (
                response.status_code == 200
                and response.json()["status"] == "pending"
            )
            if execution_state:
                await assert_receipt(client, device, execution_state, False)
            source.write_text(code, encoding="utf-8")
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
            async with asyncio.timeout(10):
                while manager.get_connection(str(device)) is first:
                    await asyncio.sleep(0.02)
            process = await start()
            second = await wait_connection(acknowledged=True, different=first)
            assert second.applied_script_policy_ack.session_id != nonce
            if execution_state:
                await heartbeat(client, device, execution_state)
                await assert_receipt(client, device, execution_state, True)
            async with sessions() as db:
                assert (await db.get(DeviceScriptPolicy, device)).revision == 1
            response = await client.post(
                path + "/script-policy/revoke",
                json=dict(expected_policy_revision=1),
            )
            assert response.status_code == 200, response.text
            assert (
                response.json()["status"] == "revoked"
                and response.json()["policy"]["revision"] == 2
            )
            third = await wait_connection(different=second)
            assert (
                third.script_policy_required
                and third.applied_script_policy_ack is None
            )
            tools = await MCPProxy().handle_list_tools(str(device))
            assert "error" in tools
            if execution_state:
                await assert_receipt(client, device, execution_state, False)
                assert marker.read_text() == "1"
            else:
                assert not marker.exists()
    finally:
        try:
            if process is not None and process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 10)
            await unregister(str(device))
            await server.stop()
            async with asyncio.timeout(10):
                while any(
                    task is not asyncio.current_task()
                    and not task.done()
                    and getattr(task.get_coro(), "cr_frame", None) is not None
                    and task.get_coro().cr_frame.f_locals.get("self") is server
                    for task in asyncio.all_tasks()
                ):
                    await asyncio.sleep(0.02)
            assert manager.get_connection(str(device)) is None
        finally:
            config.unlink(missing_ok=True)
            (tmp_path / "token").unlink(missing_ok=True)
            if previous is None:
                app.dependency_overrides.pop(get_async_db, None)
            else:
                app.dependency_overrides[get_async_db] = previous
