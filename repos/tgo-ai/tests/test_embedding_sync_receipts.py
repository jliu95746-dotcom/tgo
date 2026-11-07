"""A partial embedding receipt must never mark every project synchronized."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.project_ai_config import ProjectAIConfig
from app.services import rag_embedding_sync_service as module
from app.services.rag_service import EmbeddingConfigBatchSyncResponse
from tests.test_rag_response_lifecycle import OTHER, PRIVATE, PROJECT, configs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["success", "partial", "failed", "transport", "invalid"]
)
async def test_background_status_matches_each_projects_receipt(
    tmp_path, monkeypatch, outcome
):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path/'embedding.sqlite'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(ProjectAIConfig.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old = datetime(2020, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        session.add_all(
            [
                ProjectAIConfig(
                    project_id=UUID(project),
                    sync_status="success",
                    sync_attempt_count=5,
                    last_sync_at=old,
                )
                for project in [PROJECT, OTHER]
            ]
        )
        await session.commit()
    failed = [OTHER] if outcome == "partial" else [PROJECT, OTHER]
    receipt = EmbeddingConfigBatchSyncResponse(
        success_count=2 if outcome == "success" else 2 - len(failed),
        failed_count=0 if outcome == "success" else len(failed),
        errors=[]
        if outcome == "success"
        else [
            {"project_id": project, "message": PRIVATE} for project in failed
        ],
    )
    if outcome == "invalid":
        receipt = receipt.model_copy(update={"failed_count": 0})
    send = AsyncMock(return_value=receipt)
    if outcome == "transport":
        send.side_effect = RuntimeError(PRIVATE)
    log = Mock()
    monkeypatch.setattr(module, "logger", log)
    monkeypatch.setattr(module, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(
        module.rag_service_client, "batch_sync_embedding_configs", send
    )
    loop = asyncio.get_running_loop()
    create_task = loop.create_task
    tasks = []

    def track(coroutine, *args, **kwargs):
        task = create_task(coroutine, *args, **kwargs)
        if coroutine.cr_code.co_name == "_runner":
            tasks.append(task)
        return task

    monkeypatch.setattr(loop, "create_task", track)
    try:
        module.fire_and_forget_embedding_sync(configs())
        assert len(tasks) == 1
        await tasks[0]
        async with sessions() as session:
            for project in [PROJECT, OTHER]:
                row = await session.get(ProjectAIConfig, UUID(project))
                success = (
                    outcome == "success"
                    or outcome == "partial"
                    and project == PROJECT
                )
                assert row.sync_status == ("success" if success else "failed")
                assert PRIVATE not in (row.sync_error or "")
                assert (row.sync_error is None) == success
                assert row.sync_attempt_count == (
                    8 if outcome in {"transport", "invalid"} else 6
                )
                assert (row.last_sync_at.year > 2020) == success
        assert send.await_count == (
            3 if outcome in {"transport", "invalid"} else 1
        )
        assert PRIVATE not in repr(log.mock_calls)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_network_retry_logs_never_include_the_exception_body(
    monkeypatch,
):
    log = Mock()
    send = AsyncMock(side_effect=RuntimeError(PRIVATE))
    monkeypatch.setattr(module, "logger", log)
    monkeypatch.setattr(
        module.rag_service_client, "batch_sync_embedding_configs", send
    )
    assert (
        await module.dispatch_to_rag_with_retry(
            configs(), max_retries=2, base_delay=0
        )
        is None
    )
    assert send.await_count == 2
    assert PRIVATE not in repr(log.mock_calls)
