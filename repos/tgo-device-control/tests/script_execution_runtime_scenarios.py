"""Exercise leased execution with signed routes and owned fixture files."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import jwt
import pytest
from sqlalchemy import func, select, text

from app.config import settings
from app.models.device import DeviceSession, DeviceSessionStep
from app.models.device_script_execution import DeviceScriptExecution
from app.services.tcp_connection_manager import tcp_connection_manager


async def exercise_execution(
    client,
    sessions,
    project,
    device,
    key,
    digest,
    marker,
    progress,
    monkeypatch,
    scenario,
):
    session, agent, execution = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    headers = {
        "Authorization": "Bearer "
        + jwt.encode(
            dict(
                sub="tgo-ai",
                project_id=str(project),
                device_id=str(device),
                session_id=str(session),
                script_policy_digest=digest,
                iat=now,
                exp=now + timedelta(minutes=3),
                aud="tgo-device-control",
                iss="tgo-internal",
            ),
            key,
            algorithm="HS256",
        )
    }
    start = await client.post(
        f"/v1/sessions/{device}/{session}/start",
        headers=headers,
        json=dict(agent_id=str(agent), agent_name="隔离脚本员工"),
    )
    assert start.status_code == 200, start.text
    path = f"/v1/devices/{device}/script-executions/{execution}"
    label = "女包<&> 'quoted' $(no-command) " + chr(92)
    body = dict(parameters=dict(label=label))
    monkeypatch.setattr(settings, "SCRIPT_BUSINESS_EXECUTION_ENABLED", False)
    assert (
        await client.post(path, headers=headers, json=body)
    ).status_code == 409
    assert (await client.post(path, json=body)).status_code == 403
    assert not marker.exists()
    monkeypatch.setattr(settings, "SCRIPT_BUSINESS_EXECUTION_ENABLED", True)
    if scenario in {"cancel", "abort"}:
        original_connection = tcp_connection_manager.get_connection(
            str(device)
        )
        operation = asyncio.create_task(
            client.post(path, headers=headers, json=body)
        )
        try:
            async with asyncio.timeout(10):
                while not progress.exists() or int(progress.read_text()) < 2:
                    await asyncio.sleep(0.02)
            # Actual progress is a positive control: the process is running.
            if scenario == "cancel":
                async with sessions() as db:
                    row = await db.get(DeviceSession, session)
                    row.lease_expires_at = (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    )
                    await db.commit()
                response = await asyncio.wait_for(operation, 5)
            else:
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 5)
                # The disconnected HTTP caller cannot save completion; the
                # next query must recover cancellation without re-dispatch.
                response = await client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert not response.json()["result_available"]
            assert response.json()["status"] == "canceled", response.text
            # Cancellation must preserve this exact authenticated connection.
            assert (
                tcp_connection_manager.get_connection(str(device))
                is original_connection
            )
            before = progress.read_text()
            await asyncio.sleep(1)
            assert progress.read_text() == before
            assert marker.read_text() == "1"
            repeated = await client.post(path, headers=headers, json=body)
            assert (
                repeated.status_code == 200
                and not repeated.json()["result_available"]
            )
            assert marker.read_text() == "1"
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        return headers, execution, session, label
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded", response.text
    assert response.json()["result_available"]
    assert response.json()["fields"] == dict(ready=True, label=label)
    assert response.json()["agent_id"] == str(agent)
    repeated = await client.post(path, headers=headers, json=body)
    assert (
        repeated.status_code == 200
        and repeated.json()["fields"] == response.json()["fields"]
    )
    assert marker.read_text() == "1"
    assert (
        await client.post(
            path,
            headers=headers,
            json=dict(parameters=dict(label="different")),
        )
    ).status_code == 409
    another = f"/v1/devices/{device}/script-executions/{uuid4()}"
    assert (
        await client.post(another, headers=headers, json=body)
    ).status_code == 409
    assert marker.read_text() == "1"
    async with sessions() as db:
        stored = await db.get(DeviceScriptExecution, (device, execution))
        assert stored.status == "succeeded" and stored.result_digest
        raw = await db.scalar(
            text(
                "SELECT row_to_json(record)::text "
                "FROM dc_script_executions record"
            )
        )
        assert (
            label not in raw
            and '"parameters"' not in raw
            and '"fields"' not in raw
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(DeviceSessionStep)
            )
            == 1
        )
        assert (await db.get(DeviceSession, session)).actions_count == 1
        # Lose only the completion save; the actual device and its encrypted
        # local receipt remain unchanged.
        (
            stored.status,
            stored.failure,
            stored.result_digest,
            stored.finished_at,
        ) = ("unknown", "dispatch_unconfirmed", None, None)
        step = await db.get(DeviceSessionStep, stored.step_id)
        step.status, step.ended_at = "running", None
        await db.commit()
    recovered = await client.get(path, headers=headers)
    assert recovered.status_code == 200, recovered.text
    assert (
        recovered.json()["status"] == "succeeded"
        and recovered.json()["fields"]["label"] == label
    )
    assert marker.read_text() == "1"
    return headers, execution, session, label


async def heartbeat(client, device, state):
    headers, _, session, _ = state
    response = await client.post(
        f"/v1/sessions/{device}/{session}/heartbeat", headers=headers
    )
    assert response.status_code == 200, response.text


async def assert_receipt(client, device, state, available):
    headers, execution, _, label = state
    response = await client.get(
        f"/v1/devices/{device}/script-executions/{execution}", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["result_available"] is available
    if available:
        assert response.json()["fields"]["label"] == label
