"""Real local Runtime HTTP errors stay safe through the API progress relay."""

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.services.plugin_progress import stream_plugin_progress


PRIVATE = "synthetic-private-plugin-runtime-transport"
SERVER = r"""
import os,sys
sys.path.insert(0,os.getcwd())
from fastapi import FastAPI
import uvicorn
from app.api import routes

async def fail(*args,**kwargs):
    raise RuntimeError("synthetic-private-plugin-runtime-transport")

routes.install_plugin_operation=fail
routes.upgrade_plugin_operation=fail
app=FastAPI()
app.include_router(routes.router)
@app.get("/_failure_contract_ready")
def ready():
    return {"probe":"plugin-failure-contract"}
uvicorn.run(app,host="127.0.0.1",port=int(sys.argv[1]),lifespan="off",log_level="error")
"""


@pytest.fixture(scope="module")
def runtime_server(tmp_path_factory):
    root = Path(__file__).resolve().parents[3]
    service = root / "repos/tgo-plugin-runtime"
    interpreter = service / ".venv/Scripts/python.exe"
    python = str(interpreter) if interpreter.is_file() else sys.executable
    scratch = tmp_path_factory.mktemp("plugin-runtime-failure")
    script = scratch / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    with socket.socket() as binding:
        binding.bind(("127.0.0.1", 0))
        port = binding.getsockname()[1]
    environment = os.environ.copy()
    environment[
        "DATABASE_URL"
    ] = "postgresql+asyncpg://snapshot:synthetic@127.0.0.1:1/snapshot"
    environment["PLUGIN_BASE_PATH"] = str(scratch / "plugins")
    environment["PYTHONIOENCODING"] = "utf-8"
    url = f"http://127.0.0.1:{port}"
    log_path = scratch / "server.log"
    with log_path.open("wb") as output:
        process = subprocess.Popen(
            [python, str(script), str(port)],
            cwd=service,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW
            if os.name == "nt"
            else 0,
        )
        try:
            deadline = time.monotonic() + 30
            with httpx.Client(timeout=0.4, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail("Runtime fixture exited before readiness")
                    try:
                        response = client.get(url + "/_failure_contract_ready")
                        if response.status_code == 200 and response.json() == {
                            "probe": "plugin-failure-contract",
                        }:
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Runtime fixture did not become ready")
            yield url, log_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def payload():
    return {
        "id": "fixture",
        "project_id": str(uuid4()),
        "name": "Fixture",
        "version": "1",
        "source": {"binary": {"url": "https://source.test/plugin.zip"}},
        "source_url": "https://source.test/plugin.yaml",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
async def test_runtime_failure_is_safe_after_actual_http_and_api_relay(
    runtime_server,
    monkeypatch,
    kind,
):
    url, log_path = runtime_server
    monkeypatch.setattr(settings, "PLUGIN_RUNTIME_URL", url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    data = payload()
    if kind == "install":
        stream = stream_plugin_progress("/plugins/install-stream", data)
    else:
        project = data.pop("project_id")
        stream = stream_plugin_progress(
            "/plugins/fixture/upgrade",
            {"latest_config": data},
            params={"project_id": project},
        )

    async def collect():
        return b"".join([chunk async for chunk in stream])

    wire = await asyncio.wait_for(collect(), timeout=10)
    event = json.loads(wire.decode().removeprefix("data:").strip())
    assert event["stage"] == "error" and isinstance(event["message"], str)
    assert PRIVATE.encode() not in wire
    assert PRIVATE not in log_path.read_text(
        encoding="utf-8", errors="replace"
    )


def test_regular_runtime_failure_returns_a_safe_http_error(runtime_server):
    url, _ = runtime_server
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.post(url + "/plugins/install", json=payload())
    assert response.status_code == 500
    assert PRIVATE not in response.text
    assert isinstance(response.json()["detail"], str)
