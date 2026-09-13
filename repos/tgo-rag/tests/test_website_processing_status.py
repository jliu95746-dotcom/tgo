"""Crawled files must reach terminal states without stale worker overwrites."""

import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.rag_service.tasks import document_processing_core as core
from src.rag_service.tasks import website_crawling as crawling


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows worker regression",
)
def test_crawl_loop_supports_subprocesses_under_selector_policy():
    # Flower changes the global Windows policy when Celery loads its commands.
    # The crawl loop must support subprocesses without changing that policy.
    previous = asyncio.get_event_loop_policy()
    selector = asyncio.WindowsSelectorEventLoopPolicy()
    loop = None
    try:
        asyncio.set_event_loop_policy(selector)
        loop = crawling.create_crawl_event_loop()

        async def run_owned_subprocess():
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "print('owned-crawl-loop')",
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            assert process.returncode == 0
            assert stdout.strip() == b"owned-crawl-loop"

        loop.run_until_complete(run_owned_subprocess())
        assert asyncio.get_event_loop_policy() is selector
    finally:
        if loop is not None:
            loop.close()
        asyncio.set_event_loop_policy(previous)


@pytest.mark.asyncio
async def test_processing_failure_updates_file_and_page_without_secret(
    monkeypatch,
):
    file_id, collection_id = uuid4(), uuid4()
    error = core.DocumentProcessingError(
        "No active embedding configuration found; token=secret",
        str(file_id),
        core.ProcessingStep.GENERATING_EMBEDDINGS,
    )
    monkeypatch.setattr(core, "_load_file_info", AsyncMock(side_effect=error))
    monkeypatch.setattr(core, "_update_file_status", AsyncMock())
    file_failure = AsyncMock()
    page_failure = AsyncMock()
    monkeypatch.setattr(core, "_handle_processing_error", file_failure)
    monkeypatch.setattr(core, "_update_website_page_status", page_failure)
    result = await core.process_file_async(file_id, collection_id)
    assert result.status == "failed"
    file_failure.assert_awaited_once()
    safe_error = file_failure.await_args.args[2]
    assert "secret" not in str(safe_error)
    assert "向量模型" in str(safe_error)
    assert result.error == str(safe_error)
    page_failure.assert_awaited_once_with(file_id, "failed", str(safe_error))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_file,should_update", [(True, True), (False, False)]
)
async def test_late_old_file_does_not_overwrite_recrawled_page(
    monkeypatch,
    current_file,
    should_update,
):
    file_id, page_id, collection_id, project_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    file_record = SimpleNamespace(
        storage_metadata={"page_id": str(page_id)},
        project_id=project_id,
        collection_id=collection_id,
    )
    page = SimpleNamespace(
        file_id=file_id if current_file else uuid4(),
        project_id=project_id,
        collection_id=collection_id,
        status="processing",
        error_message="old failure",
    )
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: file_record),
        SimpleNamespace(scalar_one_or_none=lambda: page),
    ]

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(core, "get_db_session", session)
    await core._update_website_page_status(file_id, "processed")
    assert page.status == ("processed" if should_update else "processing")
    assert page.error_message == (None if should_update else "old failure")
    assert db.commit.await_count == int(should_update)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["queued", "rejected", "claimed", "changed"])
async def test_processing_state_is_saved_before_task_dispatch(
    monkeypatch, tmp_path, outcome,
):
    from src.rag_service.tasks.document_processing import process_file_task

    page_id, collection_id, project_id = uuid4(), uuid4(), uuid4()
    info = crawling.PageInfo(
        page_id,
        "https://example.test/a",
        0,
        collection_id,
        project_id,
        {},
    )
    monkeypatch.setattr(
        crawling,
        "_load_page_and_config",
        AsyncMock(
            return_value=(info, crawling.CrawlConfig.from_dict({}), None),
        ),
    )
    result = SimpleNamespace(
        title="fixture",
        content_markdown="fixture content",
        content_length=15,
        content_hash="owned",
        meta_description=None,
        http_status_code=200,
        links=[],
    )
    monkeypatch.setattr(
        crawling.WebCrawlerService,
        "crawl_page",
        AsyncMock(
            return_value=result,
        ),
    )
    monkeypatch.setattr(crawling.settings, "upload_dir", str(tmp_path))
    page = SimpleNamespace(id=page_id)
    db = AsyncMock()
    db.add = lambda record: None
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: page)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(crawling, "get_db_session", session)
    events = []

    async def prepare(source):
        assert source.project_id == project_id
        assert source.storage_metadata["page_id"] == str(page_id)
        events.append("processing")
        return outcome != "changed"

    monkeypatch.setattr(crawling, "prepare_website_dispatch", prepare)
    failure = AsyncMock(return_value=outcome == "rejected")
    monkeypatch.setattr(crawling, "fail_website_dispatch", failure)

    def dispatch(*args):
        events.append("dispatch")
        if outcome in {"rejected", "claimed"}:
            raise RuntimeError("private-broker-credential")

    monkeypatch.setattr(process_file_task, "delay", dispatch)
    result = await crawling.crawl_page_async(page_id, auto_discover=False)
    assert events == (["processing"] if outcome == "changed"
                      else ["processing", "dispatch"])
    expected = {"queued": "success", "rejected": "failed",
                "claimed": "skipped", "changed": "skipped"}
    assert result["status"] == expected[outcome]
    assert "private-broker-credential" not in str(result)
    if outcome in {"rejected", "claimed"}:
        failure.assert_awaited_once()
        source, safe_error = failure.await_args.args
        assert source.project_id == project_id
        assert safe_error == "知识库处理任务未能提交，请稍后重新抓取。"
    else:
        failure.assert_not_awaited()
