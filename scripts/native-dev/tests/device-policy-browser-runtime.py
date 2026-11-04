"""Owned browser acceptance runtime: real Device Control and synthetic TCP peer."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from uuid import UUID, uuid4

from device_policy_runtime_cleanup import require_listener_stopped

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-device-control"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.device import Device  # noqa: E402
from app.models.device_read_policy import DeviceReadPolicy  # noqa: E402
from app.models.device_screen_policy import DeviceScreenPolicy  # noqa: E402
from app.schemas.device_screen_policy import (
    ScreenGrantStatus,
    ScreenPolicy,
)  # noqa: E402
from app.services.device_read_policy import read_policy_digest  # noqa: E402
from app.services.device_screen_policy import validate_local_screen_scope  # noqa: E402


def synthetic_grant(active: bool, grant_id: str) -> ScreenGrantStatus:
    if not active:
        return ScreenGrantStatus(protocol_version=1, active=False)
    return ScreenGrantStatus(
        protocol_version=1,
        active=True,
        grant_id=grant_id,
        width=640,
        height=480,
        dpi=120,
        allow_click=True,
        allow_text=True,
        max_actions=10,
        used_actions=0,
        click_regions=[{"x": 10, "y": 10, "width": 200, "height": 60}],
    )


async def run(project: UUID, http_port: int, tcp_port: int) -> None:
    url = make_url(settings.database_url_async)
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Only loopback PostgreSQL is permitted")
    for port in (http_port, tcp_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    identity = uuid4().hex
    database = "tgo_device_policy_ui_" + identity
    assert re.fullmatch(r"tgo_device_policy_ui_[a-f0-9]{32}", database)
    directory = ROOT / ".tmp/native-dev" / ("device-policy-ui-" + identity)
    directory.mkdir(parents=True, exist_ok=False)
    device_id = uuid4()
    token = uuid4().hex + uuid4().hex
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "directory": str(directory),
        "device_id": str(device_id),
        "peer": "synthetic TCP client, no actual Windows screen or file actions",
        "ready": False,
        "verification_completed": False,
        "owned_database_removed": False,
        "owned_server_exited": False,
        "observed_methods": [],
        "screen_policy_acknowledged": False,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    pointer = ROOT / ".tmp/native-dev/device-policy-ui-current.json"

    def save():
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        (directory / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        pointer.write_text(json.dumps(report, indent=2), encoding="utf-8")

    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    owned_url = url.set(database=database)
    engine = create_async_engine(owned_url, hide_parameters=True)
    env = {
        **os.environ,
        "DATABASE_URL": owned_url.render_as_string(hide_password=False),
        "HOST": "127.0.0.1",
        "PORT": str(http_port),
        "TCP_RPC_HOST": "127.0.0.1",
        "TCP_RPC_PORT": str(tcp_port),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    server = None
    created = False
    writer = None
    peer_task = None
    heartbeat = None
    logs = []
    grant_id = uuid4().hex
    grant_active = False

    async def peer(authorize: bool):
        nonlocal writer, grant_active, grant_id, heartbeat
        grant_active = authorize
        if authorize:
            grant_id = uuid4().hex
        reader, writer = await asyncio.open_connection("127.0.0.1", tcp_port)

        async def send(message):
            writer.write((json.dumps(message) + "\n").encode())
            await writer.drain()

        await send(
            {
                "jsonrpc": "2.0",
                "id": -1,
                "method": "auth",
                "params": {
                    "deviceToken": token,
                    "deviceInfo": {
                        "name": "本机授权页面验收（模拟设备）",
                        "version": "ui-fixture",
                        "os": "Windows",
                    },
                },
            }
        )
        auth = json.loads(await asyncio.wait_for(reader.readline(), timeout=15))
        assert auth.get("result", {}).get("deviceId") == str(device_id)
        report["authenticated_connections"] = (
            report.get("authenticated_connections", 0) + 1
        )
        report["synthetic_grant_ready"] = authorize
        save()

        async def beats():
            while not writer.is_closing():
                await asyncio.sleep(10)
                await send({"jsonrpc": "2.0", "id": -2, "method": "ping", "params": {}})

        heartbeat = asyncio.create_task(beats())
        try:
            while raw := await reader.readline():
                message = json.loads(raw)
                method = message.get("method")
                if not method:
                    continue
                report["observed_methods"].append(method)
                params = message.get("params", {})
                error = None
                if method == "policy/apply":
                    grant_active = False
                    result = {key: params[key] for key in ("revision", "digest")}
                elif method == "screen/status":
                    result = synthetic_grant(grant_active, grant_id).model_dump(
                        mode="json"
                    )
                elif method == "screen/policy/apply":
                    scope = ScreenPolicy.model_validate(params)
                    local = synthetic_grant(grant_active, grant_id)
                    assert validate_local_screen_scope(local, scope)
                    result = {
                        key: params[key]
                        for key in (
                            "protocol_version",
                            "revision",
                            "grant_id",
                            "digest",
                        )
                    }
                    report["screen_policy_acknowledged"] = True
                    report["last_scope"] = {
                        key: params[key]
                        for key in (
                            "revision",
                            "max_actions",
                            "allow_click",
                            "allow_text",
                            "click_regions",
                        )
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "screen_stop" if grant_active else "fs_read",
                                "description": "synthetic UI fixture",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    }
                elif method == "tools/call":
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": "Synthetic fixture refuses real operations",
                            }
                        ],
                        "isError": True,
                    }
                elif method == "ping":
                    result = {"status": "ok"}
                else:
                    result = None
                    error = {"code": -32601, "message": "Unknown fixture method"}
                if "id" in message:
                    await send(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            **({"error": error} if error else {"result": result}),
                        }
                    )
                else:
                    report["notifications_received"] = (
                        report.get("notifications_received", 0) + 1
                    )
                save()
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            grant_active = False
            report["synthetic_grant_ready"] = False
            save()

    try:
        async with admin.connect() as db:
            assert not await db.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"),
                {"name": database},
            )
            await db.execute(text('CREATE DATABASE "' + database + '"'))
        created = True
        migration_log = directory / "migration.log"
        with migration_log.open("wb") as output:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=SERVICE,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        assert result.returncode == 0
        async with sessions() as db:
            assert (
                await db.scalar(text("SELECT version_num FROM dc_alembic_version"))
                == "0012_device_read_revocation"
            )
            db.add(
                Device(
                    id=device_id,
                    project_id=project,
                    device_name="本机授权页面验收（模拟设备）",
                    os="Windows",
                    device_token=token,
                )
            )
            db.add(
                DeviceReadPolicy(
                    device_id=device_id,
                    project_id=project,
                    revision=1,
                    enabled=True,
                    allowed_directory=r"C:\TGO-Owned-UI-Fixture",
                    max_lines=10,
                    max_offset=100,
                    digest=read_policy_digest(1, r"C:\TGO-Owned-UI-Fixture", 10, 100),
                )
            )
            await db.commit()
        stdout = (directory / "server.out.log").open("wb")
        stderr = (directory / "server.err.log").open("wb")
        logs.extend([stdout, stderr])
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(http_port),
            ],
            cwd=SERVICE,
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(60):
                assert server.poll() is None
                try:
                    if (
                        await client.get(
                            f"http://127.0.0.1:{http_port}/health", timeout=2
                        )
                    ).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Owned device server did not become healthy")
        peer_task = asyncio.create_task(peer(False))
        for _ in range(60):
            if peer_task.done():
                await peer_task
                raise RuntimeError("Synthetic peer ended before authentication")
            if report.get("authenticated_connections"):
                break
            await asyncio.sleep(0.25)
        assert report.get("authenticated_connections")
        report.update(
            ready=True, server_pid=server.pid, http_port=http_port, tcp_port=tcp_port
        )
        save()
        print(
            "Owned UI runtime ready; see .tmp/native-dev/device-policy-ui-current.json",
            flush=True,
        )
        deadline = asyncio.get_running_loop().time() + 1200
        command_path = directory / "command.json"
        while asyncio.get_running_loop().time() < deadline:
            assert server.poll() is None
            command = (
                json.loads(command_path.read_text(encoding="utf-8-sig"))
                if command_path.exists()
                else {}
            )
            if command.get("stop"):
                break
            if command.get("authorize_screen") and not report.get(
                "screen_authorization_command_used"
            ):
                if peer_task and not peer_task.done():
                    await asyncio.sleep(0.25)
                    continue
                if peer_task:
                    await peer_task
                async with sessions() as db:
                    row = await db.get(DeviceReadPolicy, device_id)
                    assert row and row.enabled is False and row.revision == 2
                report["screen_authorization_command_used"] = True
                peer_task = asyncio.create_task(peer(True))
                save()
            await asyncio.sleep(0.25)
        else:
            raise TimeoutError("Owned browser test exceeded its 20-minute deadline")
        async with sessions() as db:
            file = await db.get(DeviceReadPolicy, device_id)
            screen = await db.get(DeviceScreenPolicy, device_id)
            report["file_revoked"] = bool(
                file and file.enabled is False and file.revision == 2
            )
            report["screen_revoked"] = bool(
                screen and screen.enabled is False and screen.revision == 2
            )
            report["verification_completed"] = bool(
                report["file_revoked"]
                and report["screen_revoked"]
                and report["screen_policy_acknowledged"]
            )
            if not report["verification_completed"]:
                raise RuntimeError("Browser acceptance steps are incomplete")
    except BaseException as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        if peer_task:
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
        if server:
            if server.poll() is None:
                if os.name == "nt":
                    # The venv redirector owns a Python child on Windows.
                    # This live Popen handle is our process tree, never a service
                    # selected only by name or by a stale pid file.
                    await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    server.terminate()
                try:
                    await asyncio.to_thread(server.wait, timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    await asyncio.to_thread(server.wait, timeout=10)
            report["owned_server_exited"] = server.poll() is not None
        for stream in logs:
            stream.close()
        await engine.dispose()
        if created:
            async with admin.connect() as db:
                await db.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": database},
                )
                await db.execute(text('DROP DATABASE "' + database + '"'))
                report["owned_database_removed"] = not bool(
                    await db.scalar(
                        text("SELECT 1 FROM pg_database WHERE datname=:name"),
                        {"name": database},
                    )
                )
        await admin.dispose()
        report["test_token_in_logs"] = any(
            token in path.read_text(errors="replace")
            for path in directory.glob("*.log")
        )
        # Persist database cleanup before checking listeners. Windows may retain
        # TIME_WAIT after the owned process has exited; bind() is not an exit test.
        try:
            for port in (http_port, tcp_port):
                require_listener_stopped(port)
            report["owned_listeners_stopped"] = True
        finally:
            save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--tcp-port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.http_port, args.tcp_port))
