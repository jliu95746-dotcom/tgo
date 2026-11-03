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


@pytest.mark.asyncio
async def test_staff_file_update_uses_authenticated_tenant_and_actor_without_review(monkeypatch):
    from datetime import UTC, datetime
    project, reference = uuid4(), uuid4()
    result = dict(id=str(uuid4()), number=2, state='processing', author='员工',
                  created_at=datetime.now(UTC).isoformat(), published_by=None,
                  published_at=None, restored_from=None, error=None, content={}, preview='')
    proxy = AsyncMock(return_value=result)
    monkeypatch.setattr(endpoint.rag_client, 'knowledge_version_request', proxy)
    await endpoint.change('file', reference, VersionChangeRequest(action='publish'),
                          SimpleNamespace(role='agent', project_id=project, username='员工'))
    args = proxy.await_args.args
    assert args[:3] == ('POST', f'/file/{reference}', str(project))
    assert args[3]['actor'] == '员工' and args[3]['action'] == 'publish'
