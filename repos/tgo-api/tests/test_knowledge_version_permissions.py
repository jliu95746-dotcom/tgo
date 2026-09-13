"""The browser cannot choose its tenant, author or publish permission."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import knowledge_versions as endpoint
from app.schemas.knowledge_versions import VersionChangeRequest


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['publish', 'restore', 'discard'])
async def test_staff_cannot_publish_restore_or_discard(operation, monkeypatch):
    proxy = AsyncMock()
    monkeypatch.setattr(endpoint.rag_client, 'knowledge_version_request', proxy)
    with pytest.raises(HTTPException) as caught:
        await endpoint.transition(uuid4(), operation, SimpleNamespace(role='agent', project_id=uuid4(), username='员工'))
    assert caught.value.status_code == 403
    proxy.assert_not_awaited()


@pytest.mark.asyncio
async def test_staff_cannot_auto_publish_a_change(monkeypatch):
    proxy = AsyncMock()
    monkeypatch.setattr(endpoint.rag_client, 'knowledge_version_request', proxy)
    with pytest.raises(HTTPException) as caught:
        await endpoint.change('qa', uuid4(), VersionChangeRequest(action='publish'), SimpleNamespace(role='agent'))
    assert caught.value.status_code == 403
    proxy.assert_not_awaited()


def test_public_request_rejects_spoofed_author_and_project():
    for field in ['actor', 'project_id', 'snapshot']:
        with pytest.raises(ValidationError):
            VersionChangeRequest.model_validate({field: 'forged'})
