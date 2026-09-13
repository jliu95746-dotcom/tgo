import io
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from uuid import uuid4

import pytest
import httpx
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from app.api.v1.endpoints.provider_setup import probe_media
from app.schemas.media_probe import MediaProbeResult
from app.services.ai_client import AIServiceClient


@pytest.mark.asyncio
async def test_probe_enforces_provider_project_and_closes_file(monkeypatch):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    forward = AsyncMock()
    monkeypatch.setattr('app.api.v1.endpoints.provider_setup.ai_client.probe_media_model', forward)
    file = UploadFile(io.BytesIO(b'image'))
    with pytest.raises(HTTPException) as error:
        await probe_media(uuid4(), 'model', 'ocr', file, db, SimpleNamespace(project_id=uuid4()))
    assert error.value.status_code == 404
    forward.assert_not_called()
    assert file.file.closed
    filters = db.query.return_value.filter.call_args.args
    assert any('project_id' in str(value) for value in filters)


@pytest.mark.asyncio
async def test_probe_forwards_selected_model_without_writes(monkeypatch):
    db = MagicMock()
    result = MediaProbeResult(success=True, message='ok', output='A1001')
    forward = AsyncMock(return_value=result)
    monkeypatch.setattr('app.api.v1.endpoints.provider_setup.ai_client.probe_media_model', forward)
    project, provider = uuid4(), uuid4()
    file = UploadFile(io.BytesIO(b'image'), headers=Headers({'content-type': 'image/png'}))
    assert await probe_media(provider, 'chosen', 'ocr', file, db, SimpleNamespace(project_id=project)) == result
    assert forward.call_args.kwargs['model_id'] == 'chosen'
    assert forward.call_args.kwargs['project_id'] == str(project)
    db.commit.assert_not_called()
    assert file.file.closed


@pytest.mark.asyncio
async def test_client_forwards_file_and_internal_auth(monkeypatch):
    client = AIServiceClient()
    send = AsyncMock(return_value=httpx.Response(200, json={'success': False, 'message': 'network', 'error_code': 'network'}))
    monkeypatch.setattr(client, '_make_request', send)
    result = await client.probe_media_model(project_id='project', provider_id='provider', model_id='chosen', capability='asr', content=b'voice', mime_type='audio/wav')
    assert not result.success and result.error_code == 'network'
    assert send.call_args.kwargs['files']['file'][1] == b'voice'
    assert send.call_args.kwargs['extra_headers']['X-Project-Id'] == 'project'
    assert send.call_args.kwargs['form_data']['model_id'] == 'chosen'
