"""Employee workflow authorization must fail closed on a private AI hop."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import settings
from app.integrations.ai_client import AIClient
from app.integrations.http_client import HttpClient
from app.schemas.execution import WorkflowExecuteRequest


def reference() -> tuple[str, str, str, int, str, str]:
    return str(uuid4()), str(uuid4()), str(uuid4()), 2, str(uuid4()), "a" * 64


@pytest.mark.asyncio
async def test_private_binding_client_sends_exact_scope_and_accepts_true_receipt(
    monkeypatch,
):
    project_id, agent_id, workflow_id, version, release_id, digest = reference()
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-workflow-key"))
    post = AsyncMock(
        return_value=SimpleNamespace(
            status_code=200,
            json=lambda: {"authorized": True},
        )
    )
    monkeypatch.setattr(
        HttpClient, "get_client", AsyncMock(return_value=SimpleNamespace(post=post))
    )

    await AIClient.verify_employee_workflow_binding(
        project_id,
        agent_id,
        workflow_id,
        version,
        release_id,
        digest,
    )

    _, kwargs = post.call_args
    assert kwargs["json"] == {
        "project_id": project_id,
        "agent_id": agent_id,
        "workflow_id": workflow_id,
        "release_version": version,
        "release_id": release_id,
        "release_digest": digest,
    }
    assert kwargs["headers"] == {
        "X-Internal-API-Key": "synthetic-workflow-key",
        "X-Project-Id": project_id,
    }
    assert kwargs["timeout"] == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,receipt,error",
    [
        (409, {}, PermissionError),
        (403, {}, PermissionError),
        (500, {}, RuntimeError),
        (200, {"authorized": False}, RuntimeError),
        (200, {}, RuntimeError),
    ],
)
async def test_private_binding_client_rejects_denial_and_invalid_receipts(
    monkeypatch,
    status,
    receipt,
    error,
):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-workflow-key"))
    post = AsyncMock(
        return_value=SimpleNamespace(
            status_code=status,
            json=lambda: receipt,
        )
    )
    monkeypatch.setattr(
        HttpClient, "get_client", AsyncMock(return_value=SimpleNamespace(post=post))
    )
    with pytest.raises(error):
        await AIClient.verify_employee_workflow_binding(*reference())


@pytest.mark.asyncio
async def test_private_binding_client_rejects_outage_and_missing_secret(monkeypatch):
    get_client = AsyncMock()
    monkeypatch.setattr(HttpClient, "get_client", get_client)
    monkeypatch.setattr(settings, "SECRET_KEY", None)
    with pytest.raises(RuntimeError, match="未配置"):
        await AIClient.verify_employee_workflow_binding(*reference())
    get_client.assert_not_awaited()

    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-workflow-key"))
    post = AsyncMock(side_effect=httpx.ConnectError("offline"))
    get_client.return_value = SimpleNamespace(post=post)
    with pytest.raises(RuntimeError, match="无法核验"):
        await AIClient.verify_employee_workflow_binding(*reference())

    project_id, _, workflow_id, version, release_id, digest = reference()
    with pytest.raises(PermissionError, match="身份"):
        await AIClient.verify_employee_workflow_binding(
            project_id, "invalid-agent", workflow_id, version, release_id, digest
        )


def test_employee_execution_schema_requires_complete_fixed_production_release():
    agent_id = uuid4()
    for payload in (
        {"employee_agent_id": agent_id},
        {"employee_agent_id": agent_id, "execution_mode": "production"},
        {
            "employee_agent_id": agent_id,
            "execution_mode": "draft_test",
            "release_version": 2,
            "expected_release_id": str(uuid4()),
            "expected_release_digest": "a" * 64,
        },
    ):
        with pytest.raises(ValidationError):
            WorkflowExecuteRequest.model_validate(payload)
