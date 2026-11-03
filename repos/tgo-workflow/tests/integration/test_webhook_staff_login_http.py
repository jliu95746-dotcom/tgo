"""Use the real employee password login, without a token-issuing test route."""

import asyncio
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_formal_staff_login_without_test_token_endpoint(tmp_path):
    service = Path(__file__).parents[2]
    api = service.parents[1] / "repos/tgo-api"
    python = api / ".venv/Scripts/python.exe"
    assert python.is_file(), "Native API dependencies are required"
    prefix = "webhook-test-" + uuid4().hex + ":"
    password = secrets.token_urlsafe(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "WEBHOOK_TEST_PREFIX": prefix,
        "WEBHOOK_TEST_API_DB": str(tmp_path / "api-identity.sqlite"),
        "WEBHOOK_TEST_PROJECT": str(uuid4()),
        "WEBHOOK_TEST_ACTOR": str(uuid4()),
        "WEBHOOK_TEST_PASSWORD": password,
        "SAAS_ENABLED": "false",
        "SAAS_SMTP_HOST": "",
        "SAAS_BILLING_ENABLED": "false",
        "WUKONGIM_ENABLED": "false",
        "PYTHONUTF8": "1",
    }
    bootstrap = Path(__file__).with_name("webhook_process_bootstrap.py")
    with (tmp_path / "api.log").open("wb") as log:
        process = subprocess.Popen(
            [str(python), str(bootstrap), "api", "--port", str(port)],
            cwd=api, env=environment, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=15, trust_env=False,
            ) as client:
                deadline = time.monotonic() + 90
                while True:
                    assert process.poll() is None, "Owned listener exited"
                    try:
                        if (await client.get("/health")).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    assert time.monotonic() < deadline, "Listener timed out"
                    await asyncio.sleep(0.2)
                rejected = await client.post(
                    "/v1/staff/login",
                    data={"username": prefix[:-1], "password": "incorrect"},
                )
                assert rejected.status_code == 401
                logged_in = await client.post(
                    "/v1/staff/login",
                    data={"username": prefix[:-1], "password": password},
                )
                assert logged_in.status_code == 200
                assert logged_in.json()["token_type"] == "bearer"
                assert logged_in.json()["access_token"]
                bypass = await client.post("/_test/credentials")
                assert bypass.status_code == 404
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            assert process.poll() is not None
