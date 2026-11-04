"""Serve actual Control routes for an independent AI Python consumer."""

import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from sqlalchemy import func, select

from app.config import settings
from app.main import app
from app.models.device import DeviceSession, DeviceSessionStep
from app.models.device_script_execution import DeviceScriptExecution


async def exercise_ai_client(
    sessions,
    policy,
    descriptor,
    marker,
    tmp_path,
    monkeypatch,
    key,
    *,
    trial=False,
    saved_case=False,
    cancel_progress=None,
):
    root = Path(__file__).resolve().parents[3]
    python = root / "repos/tgo-ai/.venv/Scripts/python.exe"
    worker = (
        root
        / "repos/tgo-ai/tests"
        / (
            "script_trial_live_worker.py"
            if trial
            else "script_execution_live_worker.py"
        )
    )
    assert python.is_file() and worker.is_file()
    execution, session, agent, workflow = (uuid4() for _ in range(4))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        log_level="critical",
        access_log=False,
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    fixture = tmp_path / "owned-ai-client.json"
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                    raise AssertionError("Owned HTTP listener did not start")
                await asyncio.sleep(0.02)
        label = "隔离女包 <&> 'quoted' $(no-command) " + chr(92)
        fixture.write_text(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{port}",
                    "test_secret": key,
                    "policy": policy,
                    "descriptor": descriptor,
                    "label": label,
                    "agent_id": str(agent),
                    "session_id": str(session),
                    "workflow_execution_id": str(workflow),
                    "execution_id": str(execution),
                    "saved_case": saved_case,
                    "cancel_progress": (
                        str(cancel_progress) if cancel_progress else None
                    ),
                }
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["TGO_SCRIPT_AI_LOOPBACK_TEST"] = "owned-runtime-only"
        monkeypatch.setattr(
            settings, "SCRIPT_BUSINESS_EXECUTION_ENABLED", True
        )
        process = await asyncio.create_subprocess_exec(
            str(python),
            "-X",
            "utf8",
            str(worker),
            str(fixture),
            cwd=str(python.parents[2]),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), 120 if trial else 60
        )
        assert process.returncode == 0, stderr.decode(
            "utf-8", errors="replace"
        )
        result = json.loads(stdout)
        if trial:
            assert result["same_request"]
            assert result["comparison"] == (
                "failed" if cancel_progress else "matched"
            )
            if cancel_progress:
                assert result["stop_status"] == "confirmed"
            if saved_case:
                assert result["evidence_retained"] is True
            execution, session = UUID(result["execution_id"]), UUID(
                result["session_id"]
            )
        else:
            assert result == {"same_receipt": True, "session_closed": True}
        assert marker.read_text() == "1"
        if cancel_progress:
            before = cancel_progress.read_text()
            await asyncio.sleep(1)
            assert cancel_progress.read_text() == before
        async with sessions() as db:
            row = await db.get(
                DeviceScriptExecution, (UUID(policy["device_id"]), execution)
            )
            assert (
                row.status == ("canceled" if cancel_progress else "succeeded")
                and row.session_id == session
            )
            assert row.agent_id == agent and row.workflow_execution_id == (
                None if trial else workflow
            )
            saved = await db.get(DeviceSession, session)
            assert (
                saved.status
                == ("cancelled" if cancel_progress else "completed")
                and saved.actions_count == 1
            )
            assert (
                await db.scalar(
                    select(func.count()).select_from(DeviceSessionStep)
                )
                == 1
            )
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, 10)
        finally:
            listener.close()
            fixture.unlink(missing_ok=True)
