"""Embedding writes retain failures without logging private error details."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.rag_service.routers import embedding_config as module
from src.rag_service.schemas.embedding_config import (
    EmbeddingConfigBatchSyncRequest,
    EmbeddingConfigCreate,
)

PRIVATE = "SYNTHETIC-DATABASE-PARAMETER-CREDENTIAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_index", [0, 1])
async def test_failed_upsert_hides_error_and_continues_next_item(
    monkeypatch, failure_index
):
    projects = [uuid4(), uuid4()]
    request = EmbeddingConfigBatchSyncRequest(
        configs=[
            EmbeddingConfigCreate(
                project_id=project,
                provider="openai",
                model="fixture",
                api_key=PRIVATE,
            )
            for project in projects
        ]
    )
    db = Mock()

    @asynccontextmanager
    async def savepoint():
        yield

    db.begin_nested.side_effect = savepoint
    result = Mock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock(
        side_effect=[
            RuntimeError(PRIVATE) if index == failure_index else None
            for index in range(2)
        ]
    )
    log = Mock()
    monkeypatch.setattr(module, "logger", log)
    receipt = await module.batch_sync_embedding_configs(request, db)
    assert receipt.success_count == receipt.failed_count == 1
    assert receipt.errors[0]["project_id"] == str(projects[failure_index])
    assert db.flush.await_count == 2
    assert PRIVATE not in receipt.model_dump_json()
    assert PRIVATE not in repr(log.mock_calls)
