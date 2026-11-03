"""Workflow must confirm a current human approver through the API authority."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.integrations import api_client


@pytest.mark.asyncio
async def test_approval_assignee_check_accepts_only_matching_private_receipt(monkeypatch):
    project_id, assignee_id = str(uuid4()), str(uuid4())
    calls = []

    class Client:
        async def post(self, url, *, json, headers, timeout):
            calls.append((url, json, headers, timeout))
            return SimpleNamespace(status_code=200, json=lambda: {
                "project_id": project_id, "assignee_ids": [assignee_id],
            })

    async def client():
        return Client()

    monkeypatch.setattr(api_client, "settings", SimpleNamespace(
        API_INTERNAL_SERVICE_URL="http://api-internal:8001",
        SECRET_KEY=SecretStr("synthetic-internal-key"),
    ))
    monkeypatch.setattr(api_client.HttpClient, "get_client", client)
    await api_client.verify_approval_assignees(project_id, {assignee_id})
    assert calls[0][0] == "http://api-internal:8001/internal/workflows/approvals/assignees/verify"
    assert calls[0][1] == {"project_id": project_id, "assignee_ids": [assignee_id]}
    assert calls[0][2]["X-Internal-API-Key"] == "synthetic-internal-key"

    class WrongClient(Client):
        async def post(self, url, *, json, headers, timeout):
            return SimpleNamespace(status_code=200, json=lambda: {
                "project_id": str(uuid4()), "assignee_ids": [assignee_id],
            })

    async def wrong_client():
        return WrongClient()

    monkeypatch.setattr(api_client.HttpClient, "get_client", wrong_client)
    with pytest.raises(PermissionError):
        await api_client.verify_approval_assignees(project_id, {assignee_id})


@pytest.mark.asyncio
async def test_unavailable_or_revoked_approval_authority_fails_closed(monkeypatch):
    project_id, assignee_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(api_client, "settings", SimpleNamespace(
        API_INTERNAL_SERVICE_URL="http://api-internal:8001",
        SECRET_KEY=SecretStr("synthetic-internal-key"),
    ))

    async def client_with(status):
        class Client:
            async def post(self, *args, **kwargs):
                return SimpleNamespace(status_code=status)
        return Client()

    monkeypatch.setattr(api_client.HttpClient, "get_client", lambda: client_with(409))
    with pytest.raises(PermissionError):
        await api_client.verify_approval_assignees(project_id, {assignee_id})
    monkeypatch.setattr(api_client.HttpClient, "get_client", lambda: client_with(503))
    with pytest.raises(RuntimeError):
        await api_client.verify_approval_assignees(project_id, {assignee_id})
