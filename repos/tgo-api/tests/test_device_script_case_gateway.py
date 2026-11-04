"""Saved cases and evidence retain admin identity and no-store policy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import agent_device_script_cases
from app.api.company_configuration_access import require_configuration_write
from app.core.security import get_current_active_user
from app.schemas.agent_device_script_cases import (
    ScriptCaseWrite,
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
    ScriptCaseRecord,
    ScriptCaseRead,
    ScriptCaseValues,
    ScriptCasePage,
    ScriptCaseTrialResult,
    ScriptTrialEvidence,
)
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.services.ai_client import AIServiceClient, ai_client
from tests.test_agent_device_script_trials import (
    application as base_application,
    command as trial_command,
    report,
)

pytestmark = pytest.mark.asyncio


async def test_cancel_save_path_must_match_body_without_forwarding(
    monkeypatch,
):
    app, actor = base_application()
    app.include_router(
        agent_device_script_cases.router, prefix="/v1/ai/agents"
    )
    agent = uuid4()
    method, path, name, body, *_ = operation(
        "cancel_save", actor.project_id, actor.id, agent
    )
    action = AsyncMock()
    monkeypatch.setattr(ai_client, name, action)
    wire = body.model_dump(mode="json")
    wire["request_id"] = str(uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(method, path, json=wire)
    assert response.status_code == 409
    action.assert_not_awaited()


@pytest.mark.parametrize("location", ["value", "map_key", "extra_field"])
async def test_case_validation_does_not_echo_or_log_private_input(
    monkeypatch,
    caplog,
    location,
):
    app, actor = base_application()
    app.include_router(
        agent_device_script_cases.router, prefix="/v1/ai/agents"
    )
    agent = uuid4()
    write, _, _, _, _ = fixtures(actor.project_id, actor.id, agent)
    body = write.model_dump(mode="json")
    marker = "PRIVATE-CASE-VALIDATION-MARKER"
    if location == "value":
        body["parameters"] = {"label": {"secret": marker}}
    elif location == "map_key":
        body["parameters"] = {marker: {"secret": marker}}
    else:
        body[marker] = marker
    action = AsyncMock()
    monkeypatch.setattr(ai_client, "save_agent_device_script_case", action)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/v1/ai/agents/{agent}/device-script-cases",
            json=body,
        )
    assert response.status_code == 422
    assert marker not in response.text
    assert all(marker not in str(record.__dict__) for record in caplog.records)
    action.assert_not_awaited()


def fixtures(project, actor, agent):
    trial = trial_command()
    write = ScriptCaseWrite(
        **trial.model_dump(), title="合成用例", expected_revision=0
    )
    now = datetime.now(timezone.utc)
    version = ScriptCaseRecord(
        id=uuid4(),
        case_id=uuid4(),
        project_id=project,
        agent_id=agent,
        created_by=actor,
        request_id=write.request_id,
        revision=1,
        title=write.title,
        binding_id=write.binding_id,
        binding_revision=write.binding_revision,
        device_id=uuid4(),
        policy_digest="a" * 64,
        grant_digest="b" * 64,
        case_digest="c" * 64,
        status="current",
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    value = ScriptCaseValues(
        parameters=write.parameters, expected_fields=write.expected_fields
    )
    completed = report(project, actor, agent, trial)
    control = ScriptTrialControl.model_validate(
        trial.model_dump(include=set(ScriptTrialControl.model_fields))
    )
    return write, version, value, completed, control


def operation(kind, project, actor, agent):
    write, version, values, completed, control = fixtures(
        project, actor, agent
    )
    base = f"/v1/ai/agents/{agent}"
    save_control = ScriptCaseSaveControl.model_validate(
        write.model_dump(include=set(ScriptCaseSaveControl.model_fields))
    )
    save_state = ScriptCaseSaveState(
        **save_control.model_dump(),
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        status="saved",
        case=version,
        created_at=version.created_at,
        deadline_at=version.expires_at,
        completed_at=version.created_at,
        error_code=None,
    )
    routes = {
        "save_state": (
            "GET",
            base + f"/device-script-cases/requests/{write.request_id}/state",
            "agent_device_script_case_save_state",
            None,
            save_state,
        ),
        "cancel_save": (
            "POST",
            base + f"/device-script-cases/requests/{write.request_id}/cancel",
            "cancel_agent_device_script_case_save",
            save_control,
            save_state,
        ),
        "list": (
            "GET",
            base + "/device-script-cases",
            "list_agent_device_script_cases",
            None,
            ScriptCasePage(
                project_id=project,
                agent_id=agent,
                items=[version],
                total=1,
                skip=0,
                limit=20,
            ),
        ),
        "save": (
            "POST",
            base + "/device-script-cases",
            "save_agent_device_script_case",
            write,
            version,
        ),
        "read": (
            "GET",
            base + f"/device-script-cases/versions/{version.id}",
            "get_agent_device_script_case",
            None,
            ScriptCaseRead(case=version, values=values),
        ),
        "recover": (
            "GET",
            base + "/device-script-cases/requests/" + str(write.request_id),
            "agent_device_script_case_request",
            None,
            version,
        ),
        "run": (
            "POST",
            base + f"/device-script-cases/versions/{version.id}" + "/trials",
            "run_agent_device_script_case",
            control,
            ScriptCaseTrialResult(
                case_version_id=version.id, report=completed
            ),
        ),
        "evidence": (
            "GET",
            base + f"/device-script-trials/{completed.id}" + "/evidence",
            "agent_device_script_evidence",
            None,
            ScriptTrialEvidence(
                project_id=project,
                actor_id=actor,
                agent_id=agent,
                report_id=completed.id,
                status="available",
                case=version,
                values=values,
                result_fields=values.expected_fields,
                captured_at=completed.completed_at,
                expires_at=version.expires_at,
            ),
        ),
    }
    return (*routes[kind], write, version, completed)


@pytest.mark.parametrize(
    "kind",
    [
        "list",
        "save",
        "read",
        "recover",
        "run",
        "evidence",
        "save_state",
        "cancel_save",
    ],
)
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_public_case_and_evidence_routes_use_current_login(
    monkeypatch, kind, role
):
    app, staff = base_application(role)
    app.include_router(
        agent_device_script_cases.router, prefix="/v1/ai/agents"
    )
    agent = uuid4()
    method, path, name, body, result, *_ = operation(
        kind, staff.project_id, staff.id, agent
    )
    forward = AsyncMock(return_value=result)
    monkeypatch.setattr(ai_client, name, forward)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            method,
            path,
            params={"project_id": str(uuid4())},
            json=body.model_dump(mode="json") if body else None,
        )
    assert response.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        assert forward.await_args.args[:3] == (
            staff.project_id,
            staff.id,
            agent,
        )
        assert response.headers["cache-control"] == "no-store"
    else:
        forward.assert_not_awaited()


@pytest.mark.parametrize(
    "kind",
    [
        "list",
        "save",
        "read",
        "recover",
        "run",
        "evidence",
        "save_state",
        "cancel_save",
    ],
)
@pytest.mark.parametrize("guard", ["session", "configuration"])
async def test_case_routes_keep_session_and_configuration_guards(
    monkeypatch, kind, guard
):
    app, staff = base_application()
    app.include_router(
        agent_device_script_cases.router, prefix="/v1/ai/agents"
    )

    def denied():
        raise HTTPException(401 if guard == "session" else 403)

    app.dependency_overrides[
        get_current_active_user
        if guard == "session"
        else require_configuration_write
    ] = denied
    method, path, name, body, *_ = operation(
        kind, staff.project_id, staff.id, uuid4()
    )
    forward = AsyncMock()
    monkeypatch.setattr(ai_client, name, forward)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            method,
            path,
            json=body.model_dump(mode="json") if body else None,
        )
    assert response.status_code == (401 if guard == "session" else 403)
    forward.assert_not_awaited()


@pytest.mark.parametrize(
    "kind",
    [
        "list",
        "save",
        "read",
        "recover",
        "run",
        "evidence",
        "save_state",
        "cancel_save",
    ],
)
@pytest.mark.parametrize(
    "fault", [None, "project", "agent", "missing_scope", "approval"]
)
async def test_proxy_requires_complete_owned_cases_and_evidence(
    monkeypatch, kind, fault
):
    project, actor, agent = uuid4(), uuid4(), uuid4()
    _, _, name, body, result, write, version, completed = operation(
        kind, project, actor, agent
    )
    wire = result.model_dump(mode="json")
    subject = (
        wire["items"][0]
        if kind == "list"
        else wire["case"]
        if kind == "read"
        else wire["report"]
        if kind == "run"
        else wire
    )
    if fault == "project":
        subject["project_id"] = str(uuid4())
    elif fault == "agent":
        subject["agent_id"] = str(uuid4())
    elif fault == "missing_scope":
        subject.pop("scope")
    elif fault == "approval":
        subject["can_enable"] = True
    client = AIServiceClient()
    transport = AsyncMock(return_value=httpx.Response(200, json=wire))
    monkeypatch.setattr(client, "_make_request", transport)
    arguments = (
        (body,)
        if kind in {"save", "cancel_save"}
        else (version.id,)
        if kind == "read"
        else (write.request_id,)
        if kind in {"recover", "save_state"}
        else (version.id, body)
        if kind == "run"
        else (completed.id,)
        if kind == "evidence"
        else ()
    )
    if fault:
        with pytest.raises(HTTPException) as error:
            await getattr(client, name)(project, actor, agent, *arguments)
        assert error.value.status_code == 502
    else:
        await getattr(client, name)(project, actor, agent, *arguments)
    call = transport.await_args
    assert call.kwargs["extra_headers"]["X-Staff-Id"] == str(actor)
    assert len(transport.await_args_list) == 1
    if kind == "run":
        assert call.kwargs["json_data"] == body.model_dump(mode="json")
        assert "parameters" not in call.kwargs["json_data"]
