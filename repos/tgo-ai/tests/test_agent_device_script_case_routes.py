"""All retained-body routes require trusted identities and suppress caching."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_device_script_cases as module
from app.config import settings
from app.schemas.agent_device_script_cases import (
    ScriptCasePage,
    ScriptCaseSaveState,
    ScriptCaseRead,
    ScriptCaseRecord,
    ScriptCaseValues,
    ScriptTrialEvidence,
)
from app.schemas.agent_device_script_trials import ScriptTrialReport
from app.schemas.agent_device_script_reviews import (
    ScriptReviewState,
    ScriptRunCheck,
)


@pytest.mark.parametrize(
    "identity", ["valid", "missing", "public", "wrong_key", "wrong_id"]
)
@pytest.mark.asyncio
async def test_private_cases_inject_identity_and_never_cache_bodies(
    monkeypatch, identity
):
    project, actor, agent, version, report_id = (uuid4() for _ in range(5))
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "secret_key", key)
    now = datetime.now(timezone.utc)
    case = ScriptCaseRecord(
        id=version,
        case_id=uuid4(),
        project_id=project,
        agent_id=agent,
        created_by=actor,
        request_id=uuid4(),
        revision=1,
        title="只读结果测试",
        binding_id=uuid4(),
        binding_revision=1,
        device_id=uuid4(),
        policy_digest="a" * 64,
        grant_digest="b" * 64,
        case_digest="c" * 64,
        status="current",
        created_at=now,
        expires_at=now + timedelta(days=90),
    )
    request = uuid4()
    report = ScriptTrialReport(
        id=report_id,
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        request_id=request,
        device_id=case.device_id,
        execution_id=uuid4(),
        binding_id=case.binding_id,
        binding_revision=1,
        policy_digest=case.policy_digest,
        grant_digest=case.grant_digest,
        expected_digest="d" * 64,
        result_digest=None,
        status="unknown",
        error_code="result_unknown",
        remaining_runs=1,
        applicability="current",
        created_at=now,
        completed_at=None,
        expires_at=case.expires_at,
        dispatch_claimed_at=now,
    )
    values = ScriptCaseValues(
        parameters={"label": "private-marker"}, expected_fields={"ready": True}
    )
    saved = ScriptCaseSaveState(
        request_id=case.request_id,
        binding_id=case.binding_id,
        binding_revision=case.binding_revision,
        case_id=None,
        expected_revision=0,
        project_id=project,
        actor_id=actor,
        agent_id=agent,
        status="saved",
        case=case,
        created_at=now,
        deadline_at=case.expires_at,
        completed_at=now,
        error_code=None,
    )
    service = Mock(
        save_state=AsyncMock(return_value=saved),
        cancel_save=AsyncMock(return_value=saved),
        list=AsyncMock(
            return_value=ScriptCasePage(
                project_id=project,
                agent_id=agent,
                items=[case],
                total=1,
                skip=0,
                limit=20,
            )
        ),
        save=AsyncMock(return_value=case),
        by_request=AsyncMock(return_value=case),
        get=AsyncMock(return_value=ScriptCaseRead(case=case, values=values)),
        run=AsyncMock(return_value=report),
        evidence=AsyncMock(
            return_value=ScriptTrialEvidence(
                project_id=project,
                actor_id=actor,
                agent_id=agent,
                report_id=report_id,
                status="available",
                case=case,
                values=values,
                result_fields={"ready": True},
                captured_at=now,
                expires_at=case.expires_at,
            )
        ),
    )
    check = AsyncMock(return_value=ScriptRunCheck(
        review=ScriptReviewState(
            project_id=project, actor_id=actor, agent_id=agent,
            report_id=report_id, status="unreviewed", review=None,
        ),
        status="review_required", remaining_runs=None,
        checked_at=now, valid_until=None,
    ))
    reviewer = Mock(return_value=Mock(check_run=check))
    monkeypatch.setattr(module, "AgentDeviceScriptReviews", reviewer)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/agents")
    app.dependency_overrides[module.get_service] = lambda: service
    headers = {
        "X-Internal-API-Key": key,
        "X-Project-Id": str(project),
        "X-Staff-Id": str(actor),
    }
    if identity == "missing":
        headers = {}
    elif identity == "public":
        headers = {"Authorization": "Bearer public-token"}
    elif identity == "wrong_key":
        headers["X-Internal-API-Key"] = "wrong"
    elif identity == "wrong_id":
        headers["X-Staff-Id"] = "not-an-id"
    path = f"/api/v1/agents/{agent}/device-script-cases"
    control = dict(
        request_id=str(request),
        binding_id=str(case.binding_id),
        binding_revision=1,
    )
    body = dict(
        **control,
        **values.model_dump(),
        case_id=None,
        expected_revision=0,
        title=case.title,
    )
    operations = [
        (
            "GET",
            f"/api/v1/agents/{agent}/device-script-trials/"
            f"{report_id}/review/run-check",
            None,
        ),
        ("GET", path + f"/requests/{case.request_id}/state", None),
        (
            "POST",
            path + f"/requests/{case.request_id}/cancel",
            {
                "request_id": str(case.request_id),
                "binding_id": str(case.binding_id),
                "binding_revision": case.binding_revision,
                "case_id": None,
                "expected_revision": 0,
            },
        ),
        ("GET", path, None),
        ("POST", path, body),
        ("GET", path + f"/requests/{case.request_id}", None),
        ("GET", path + f"/versions/{version}", None),
        ("POST", path + f"/versions/{version}/trials", control),
        (
            "GET",
            f"/api/v1/agents/{agent}/device-script-trials/"
            f"{report_id}/evidence",
            None,
        ),
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for method, target, data in operations:
            response = await client.request(
                method,
                target + f"?project_id={uuid4()}&actor_id={uuid4()}",
                headers=headers,
                json=data,
            )
            assert response.status_code == (
                200 if identity == "valid" else 403
            )
            if identity == "valid":
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["Pragma"] == "no-cache"
        if identity == "valid":
            reviewer.assert_called_once_with(service)
            check.assert_awaited_once_with(
                project, actor, agent, report_id,
            )
        else:
            reviewer.assert_not_called()
            check.assert_not_awaited()
        for name in (
            "list",
            "save",
            "by_request",
            "get",
            "run",
            "evidence",
            "save_state",
            "cancel_save",
        ):
            if identity == "valid":
                assert getattr(service, name).await_args.args[:3] == (
                    project,
                    actor,
                    agent,
                )
            else:
                getattr(service, name).assert_not_awaited()
        if identity == "valid":
            bad = {
                **body,
                "parameters": {"label": {"secret": "private-marker"}},
            }
            response = await client.post(path, headers=headers, json=bad)
            assert (
                response.status_code == 422
                and "private-marker" not in response.text
            )
            assert service.save.await_count == 1
