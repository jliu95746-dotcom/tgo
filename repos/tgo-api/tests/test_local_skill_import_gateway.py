import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import ai_skills
from app.core.security import get_authenticated_project


@pytest.mark.asyncio
async def test_multipart_import_uses_authenticated_project(monkeypatch):
    project = SimpleNamespace(id=uuid4())
    imported = AsyncMock(return_value={
        'name': 'test-skill', 'description': 'test', 'instructions': 'test',
        'is_official': False, 'is_featured': False, 'tags': [], 'enabled': False,
        'display_name': '客服流程',
    })
    monkeypatch.setattr(ai_skills.ai_client, 'import_skill', imported)
    app = FastAPI()
    app.include_router(ai_skills.router, prefix='/skills')
    app.dependency_overrides[get_authenticated_project] = lambda: (project, '')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/skills/import', files={'file': ('SKILL.md', b'content')},
                                     data={'display_name': '客服流程', 'project_id': str(uuid4())})
        assert response.status_code == 201, response.text
        assert imported.await_args.args[0] == str(project.id)
        payload = imported.await_args.args[1]
        assert base64.b64decode(payload['content_base64']) == b'content'
        assert payload['display_name'] == '客服流程'
        imported.reset_mock()
        empty = await client.post('/skills/import', files={'file': ('SKILL.md', b'')})
        assert empty.status_code == 422
        huge = await client.post('/skills/import', files={'file': ('large.zip', b'x' * (10 * 1024 * 1024 + 1))})
        assert huge.status_code == 413
        imported.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_url_import_rejected(monkeypatch):
    app = FastAPI()
    app.include_router(ai_skills.router, prefix='/skills')
    app.dependency_overrides[get_authenticated_project] = lambda: (SimpleNamespace(id=uuid4()), '')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/skills/import', json={'github_url': 'https://example.test'})
        assert response.status_code == 422
