"""Classified reports keep provider details private and preserve receipts."""
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import exceptions
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_trials import AgentTrialRequest
from tests.test_agent_trial_reports import prepared


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("model", "blocked", "model_configuration_unavailable"),
        ("authorization", "blocked", "authorization_denied"),
        ("dependency", "failed", "dependency_unavailable"),
        ("ordinary_conflict", "failed", "execution_failed"),
        ("unexpected", "failed", "execution_failed"),
        ("timeout", "failed", "timeout"),
    ],
)
async def test_specific_safe_failure_is_durable_without_reexecuting(
    db_session,
    test_project,
    monkeypatch,
    caplog,
    kind,
    status,
    code,
):
    from app.services import agent_trials

    marker = "Authorization: synthetic-secret-provider-detail"
    factories = {
        "model": exceptions.ModelConfigurationVerificationError,
        "authorization": exceptions.AuthorizationError,
        "dependency": lambda value: exceptions.ExternalServiceError(
            "provider", value
        ),
        "ordinary_conflict": exceptions.ConflictError,
        "unexpected": RuntimeError,
        "timeout": TimeoutError,
    }
    agent, runtime, service = await prepared(
        db_session, test_project, monkeypatch
    )
    error = factories[kind](marker)
    if kind in {"model", "authorization"}:
        monkeypatch.setattr(
            AIServiceClient, "get_trial_agent", AsyncMock(side_effect=error)
        )
    else:
        runtime.run_knowledge_trial.side_effect = error
    actor = uuid4()
    request = AgentTrialRequest(request_key=uuid4(), message="企业提供什么服务？")
    report = await service.start(test_project.id, actor, agent.id, request)
    recovered = await service.start(test_project.id, actor, agent.id, request)
    assert report.id == recovered.id and recovered.error_code == code
    assert report.status == status and report.error_code == code
    assert report.answer == "" and report.can_enable is False
    if kind in {"model", "authorization"}:
        runtime.run_knowledge_trial.assert_not_awaited()
    else:
        runtime.run_knowledge_trial.assert_awaited_once()
    records = [
        record
        for record in caplog.records
        if record.name == agent_trials.__name__
    ]
    assert records and records[-1].extra_fields["failure_code"] == code
    assert records[-1].extra_fields["report_id"] == str(report.id)
    assert not records[-1].exc_info
    assert marker not in report.model_dump_json()
    assert marker not in caplog.text
    assert marker not in json.dumps(
        [record.extra_fields for record in records]
    )


@pytest.mark.asyncio
async def test_unavailable_authorization_service_is_not_a_permission_denial(
    db_session,
    test_project,
    monkeypatch,
):
    agent, runtime, service = await prepared(
        db_session, test_project, monkeypatch
    )
    actor = uuid4()
    request = AgentTrialRequest(request_key=uuid4(), message="企业提供什么服务？")
    authorize = AsyncMock(side_effect=HTTPException(503, "synthetic-secret"))
    with pytest.raises(HTTPException) as error:
        await service.start(
            test_project.id, actor, agent.id, request, authorize=authorize
        )
    assert error.value.status_code == 503 and "依赖" in error.value.detail
    assert "synthetic-secret" not in str(error.value.detail)
    report = await service.by_request(
        test_project.id, actor, agent.id, request.request_key
    )
    assert (
        report.status == "failed"
        and report.error_code == "dependency_unavailable"
    )
    recovered = await service.start(
        test_project.id, actor, agent.id, request, authorize=authorize
    )
    assert (
        recovered.id == report.id and recovered.error_code == report.error_code
    )
    authorize.assert_awaited_once()
    runtime.run_knowledge_trial.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403])
async def test_http_access_denials_keep_status_and_original_receipt(
    db_session,
    test_project,
    monkeypatch,
    status,
):
    agent, runtime, service = await prepared(
        db_session, test_project, monkeypatch
    )
    actor = uuid4()
    request = AgentTrialRequest(request_key=uuid4(), message="企业提供什么服务？")
    authorize = AsyncMock(
        side_effect=HTTPException(status, "synthetic-secret")
    )
    with pytest.raises(HTTPException) as error:
        await service.start(
            test_project.id, actor, agent.id, request, authorize=authorize
        )
    assert error.value.status_code == status
    assert "synthetic-secret" not in str(error.value.detail)
    report = await service.by_request(
        test_project.id, actor, agent.id, request.request_key
    )
    assert (
        report.status == "blocked"
        and report.error_code == "authorization_denied"
    )
    assert report.answer == "" and report.can_enable is False
    recovered = await service.start(
        test_project.id, actor, agent.id, request, authorize=authorize
    )
    assert recovered.id == report.id
    authorize.assert_awaited_once()
    runtime.run_knowledge_trial.assert_not_awaited()
