"""Quota service failure blocks processing and source bytes share one local lock."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from rag_service.services import company_resources as service


@pytest.mark.asyncio
async def test_disabled_billing_preserves_existing_processing(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(saas_enabled=False))
    assert not (await service.require_processing(uuid4())).metered


@pytest.mark.asyncio
async def test_missing_internal_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(saas_enabled=True,
        saas_billing_enabled=True, saas_internal_token=None, saas_api_internal_url=""))
    with pytest.raises(HTTPException) as error:
        await service.require_processing(uuid4())
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("additional,allowed", [(20, True), (21, False)])
async def test_capacity_counts_file_and_qa_bytes_under_one_lock(monkeypatch, additional, allowed):
    limits = service.CompanyResources(metered=True, knowledge_bytes=100, channel_limit=2, expires_at=None)
    monkeypatch.setattr(service, "require_processing", AsyncMock(return_value=limits))
    db = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), scalar=AsyncMock(side_effect=[60, 20]))
    if allowed:
        await service.ensure_capacity(db, uuid4(), additional)
    else:
        with pytest.raises(HTTPException) as error:
            await service.ensure_capacity(db, uuid4(), additional)
        assert error.value.status_code == 409
    assert 'pg_advisory_xact_lock' in str(db.execute.call_args.args[0])
    db.flush.assert_awaited_once()
