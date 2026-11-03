"""Real HTTP timeouts and terminated fixture processes cannot replay an old key."""

import asyncio
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_original_business_requests_survive_real_http_timeout_and_process_restart(
    tmp_path,
):
    root = Path(__file__).resolve().parents[3]
    directory = root / ".tmp" / ("business-fault-" + uuid4().hex)
    directory.mkdir()
    assert directory.resolve().parent == (root / ".tmp").resolve()
    server_path = Path(__file__).parent / "fixtures/business_fault_http_server.py"
    project, actor, agent, connection, proof = [uuid4() for _ in range(5)]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(Path(__file__).parents[1]),
        "DATABASE_URL": "sqlite+aiosqlite:///"
        + (directory / "state.sqlite").as_posix(),
        "BUSINESS_FAULT_DIRECTORY": str(directory),
        "BUSINESS_FAULT_PORT": str(port),
        "BUSINESS_FAULT_VALID_UNTIL": (
            datetime.now(UTC) + timedelta(hours=1)
        ).isoformat(),
    }
    environment.update(
        {
            "BUSINESS_FAULT_" + key: str(value)
            for key, value in zip(
                ("PROJECT", "ACTOR", "AGENT", "CONNECTION", "PROOF"),
                (project, actor, agent, connection, proof),
            )
        }
    )
    processes = []
    logs = []

    async def start():
        log = (directory / ("server-" + str(len(processes)) + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, str(server_path)],
            cwd=Path(__file__).parents[1],
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append(process)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as client:
            for _ in range(120):
                assert process.poll() is None, (
                    "Owned fault server exited during startup"
                )
                try:
                    response = await client.get("/health", timeout=0.5)
                    if response.status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise AssertionError("Owned fault server did not become ready")

    async def stop(process):
        assert process.args == [sys.executable, str(server_path)]
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait, 10)
        assert process.poll() is not None, "Owned process termination was not confirmed"

    def count():
        path = directory / "query-count"
        return int(path.read_text()) if path.exists() else 0

    private_order = "ISOLATED-PRIVATE-ORDER-123"
    private_session = "isolated-private-customer-session"

    def command():
        return {
            "request_id": str(uuid4()),
            "connection_id": str(connection),
            "operation": "order_query",
            "visitor_id": str(uuid4()),
            "order_no": private_order,
            "customer_session": private_session,
        }

    base = f"/api/v1/agents/{agent}/business-trials"
    try:
        process = await start()
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15
        ) as client:
            first = command()
            (directory / "hold-prepare").touch()
            with pytest.raises(httpx.ReadTimeout):
                await client.post(base, json=first, timeout=0.5)
            key = base + "/requests/" + first["request_id"]
            state = await client.get(key + "/state")
            assert state.status_code == 200 and state.json()["status"] == "preparing"
            cancelled = await client.post(
                key + "/cancel",
                json={
                    key: first[key]
                    for key in ("request_id", "connection_id", "operation")
                },
            )
            assert (
                cancelled.status_code == 200
                and cancelled.json()["status"] == "cancelled"
            )
            (directory / "hold-prepare").unlink()
            await asyncio.sleep(0.2)
            assert (
                await client.post(base, json=first)
            ).status_code == 409 and count() == 0
            print(
                "Real HTTP timeout, early cancellation and late original request: PASS"
            )
            second = command()
            (directory / "hold-prepare").touch()
            with pytest.raises(httpx.ReadTimeout):
                await client.post(base, json=second, timeout=0.5)
            key = base + "/requests/" + second["request_id"]
            assert (await client.get(key + "/state")).json()["status"] == "preparing"
            assert (
                process.poll() is None
                and (await client.get("/health")).status_code == 200
            )
            await stop(process)
            (directory / "hold-prepare").unlink()
            process = await start()
            state = await client.get(key + "/state")
            assert state.status_code == 200 and state.json()["status"] in {
                "preparing",
                "failed",
            }
            cancelled = await client.post(
                key + "/cancel",
                json={
                    key: second[key]
                    for key in ("request_id", "connection_id", "operation")
                },
            )
            assert cancelled.status_code == 200 and cancelled.json()["status"] in {
                "cancelled",
                "failed",
            }
            assert (
                await client.post(base, json=second)
            ).status_code == 409 and count() == 0
            print(
                "Verified process exit/restart and original preparation remains fenced: PASS"
            )
            third = command()
            response = await client.post(base, json=third)
            assert response.status_code == 202
            report = response.json()["id"]
            for _ in range(100):
                response = await client.get(base + "/" + report)
                if response.json()["status"] != "running":
                    break
                await asyncio.sleep(0.05)
            assert response.json()["status"] == "completed" and count() == 1
            body = {
                "request_id": str(uuid4()),
                "expected_latest_review_id": None,
                "decision": "passed",
                "reason": None,
                "checks": {
                    "ownership_checked": True,
                    "result_checked": True,
                    "readonly_scope_checked": True,
                },
            }
            reviews = base + "/" + report + "/reviews"
            key = reviews + "/requests/" + body["request_id"]
            (directory / "hold-review").touch()
            with pytest.raises(httpx.ReadTimeout):
                await client.post(reviews, json=body, timeout=0.5)
            state = await client.get(key + "/state")
            assert (
                state.status_code == 200
                and state.json()["processing"]
                and not state.json()["can_resume"]
            )
            await stop(process)
            (directory / "hold-review").unlink()
            process = await start()
            state = await client.get(key + "/state")
            assert state.status_code == 200 and not state.json()["can_resume"]
            assert (await client.post(key + "/resume")).status_code == 409
            cancelled = await client.post(key + "/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["result"] is None
            assert (await client.post(reviews, json=body)).status_code == 409 and (
                await client.get(key)
            ).status_code == 404
            assert count() == 1
            print(
                "Verified review process restart does not replay approval or its business query: PASS"
            )
            for text in (state.text, cancelled.text):
                assert private_order not in text and private_session not in text
    finally:
        for process in processes:
            await stop(process)
        for log in logs:
            log.close()
        print(
            "Owned fault processes stopped; isolated artifacts retained under ignored .tmp"
        )
