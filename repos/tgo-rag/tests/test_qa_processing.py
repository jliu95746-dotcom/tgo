"""QA processing must be atomic, idempotent and scoped to current content."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.rag_service.models import QAPair
from src.rag_service.services import qa_documents as tasks


class MemorySession:
    def __init__(self, pair):
        self.pair = pair
        self.documents = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, statement):
        if not getattr(statement, 'is_select', False):
            return None
        entity = statement.column_descriptions[0]['entity']
        row = self.pair if entity is QAPair else next(iter(self.documents), None)
        return SimpleNamespace(scalar_one_or_none=lambda: row, scalar_one=lambda: row)

    def add(self, document):
        self.documents.append(document)

    async def flush(self):
        pass

    async def delete(self, document):
        self.documents.remove(document)

    async def refresh(self, _):
        pass

    async def commit(self):
        pass


@pytest.mark.parametrize('message, expected', [
    ('No active embedding configuration found for project example', '未配置可用的向量模型'),
    ('Failed to queue QA processing: redis://user:private@localhost', '未能入队'),
    ('AuthenticationError HTTP 401: api_key=private-secret', 'HTTP 401'),
    ('unknown failure with api_key=private-secret', 'RAG worker'),
])
def test_response_failure_details_do_not_expose_credentials(message, expected):
    from datetime import datetime, timezone
    from src.rag_service.schemas.qa import QAPairResponse

    now = datetime.now(timezone.utc)
    response = QAPairResponse(
        id=uuid4(), collection_id=uuid4(), question='test', answer='test',
        question_hash='test', source_type='manual', status='failed',
        error_message=message, priority=0, created_at=now, updated_at=now,
    )
    assert expected in response.error_message
    assert 'private' not in response.error_message


@pytest.mark.parametrize('message, expected', [
    ('api_key missing; private-secret', '缺少 API Key'),
    ('base_url missing; private-secret', '缺少 base_url'),
    ('Embedding has invalid dimensions; private-secret', '无效向量'),
    ('Embedding is a zero vector; private-secret', '无效向量'),
    ('Embedding is not finite; private-secret', '无效向量'),
    ('Error code: 429 body=private-secret', 'HTTP 429'),
    ('status code 503 body=private-secret', 'HTTP 503'),
    ('HTTP 200 body=private-secret', 'RAG worker'),
    ('后台任务未能入队，请检查 RAG worker 和 Redis 后重试。private-secret', 'RAG worker'),
])
def test_failure_classification_is_safe_and_idempotent(message, expected):
    from src.rag_service.services.qa_errors import safe_qa_failure

    reason = safe_qa_failure(message)
    assert expected in reason and 'private-secret' not in reason
    assert safe_qa_failure(reason) == reason


@pytest.fixture
def fixture(monkeypatch):
    pair = QAPair(id=uuid4(), project_id=uuid4(), collection_id=uuid4(),
                  question='校验包是什么颜色？', answer='校验包是绿色。',
                  status='pending', document_id=None, deleted_at=None)
    session = MemorySession(pair)
    embedding = SimpleNamespace(
        generate_embedding=AsyncMock(return_value=[0.1] * 1536),
        get_embedding_model=Mock(return_value='fixture-model'),
        embeddings_client=Mock(),
    )
    monkeypatch.setattr(tasks, 'get_db_session', lambda: session)
    monkeypatch.setattr(
        tasks, 'get_embedding_service_for_project', AsyncMock(return_value=embedding),
    )
    return pair, session, embedding


@pytest.mark.asyncio
async def test_failed_vector_write_is_not_processed(fixture):
    pair, _, embedding = fixture
    embedding.generate_embedding.side_effect = RuntimeError('provider unavailable')
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert result['success'] is False
    assert pair.status == 'failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('message, expected', [
    ('AuthenticationError HTTP 401: api_key=private-secret', 'HTTP 401'),
    ('No active embedding configuration; token=private-secret', '未配置可用的向量模型'),
    ('database://user:private-secret@host failed', 'RAG worker'),
])
async def test_worker_errors_are_safe_before_storage_and_logging(
    fixture, monkeypatch, message, expected,
):
    from src.rag_service.schemas.qa import QAPairResponse

    pair, _, embedding = fixture
    embedding.generate_embedding.side_effect = RuntimeError(message)
    logger = Mock()
    monkeypatch.setattr(tasks, 'logger', logger)
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert not result['success'] and pair.status == 'failed'
    assert expected in result['error']
    assert pair.error_message == result['error']
    assert QAPairResponse.public_failure_reason(pair.error_message) == result['error']
    assert 'private-secret' not in repr((result, pair.error_message, logger.mock_calls))
    assert logger.error.call_args.kwargs['error_type'] == 'RuntimeError'


@pytest.mark.parametrize('batch', [False, True])
def test_task_wrapper_does_not_return_or_log_raw_errors(monkeypatch, batch):
    from src.rag_service.tasks import qa_processing

    logger = Mock()
    monkeypatch.setattr(qa_processing, 'logger', logger)
    monkeypatch.setattr(qa_processing, 'reset_db_state', Mock(
        side_effect=RuntimeError('database://user:private-secret@host'),
    ))
    task = (qa_processing.process_qa_pairs_batch_task if batch
            else qa_processing.process_qa_pair_task)
    identifier = [str(uuid4())] if batch else str(uuid4())
    result = task.run(identifier, str(uuid4()))
    assert not result['success'] and 'RAG worker' in result['error']
    assert 'private-secret' not in repr((result, logger.mock_calls))


@pytest.mark.asyncio
async def test_queue_failure_is_safe_before_storage_and_logging(fixture, monkeypatch):
    from src.rag_service.routers import qa
    from src.rag_service.schemas.qa import QAPairUpdateRequest, QAPairResponse
    from src.rag_service.tasks.qa_processing import process_qa_pair_task

    pair, session, _ = fixture
    pair.status = 'failed'
    logger = Mock()
    monkeypatch.setattr(qa, 'logger', logger)
    monkeypatch.setattr(process_qa_pair_task, 'delay', Mock(
        side_effect=RuntimeError('redis://user:private-secret@host'),
    ))
    monkeypatch.setattr(qa.QAPairResponse, 'model_validate', lambda value: value)
    await qa.update_qa_pair(
        pair.id, QAPairUpdateRequest(answer=pair.answer), pair.project_id, session,
    )
    assert pair.status == 'failed' and '未能入队' in pair.error_message
    assert (
        QAPairResponse.public_failure_reason(pair.error_message) == pair.error_message
    )
    assert 'private-secret' not in repr((pair.error_message, logger.mock_calls))


@pytest.mark.asyncio
@pytest.mark.parametrize('batch', [False, True])
async def test_create_queue_failure_never_persists_raw_error(monkeypatch, batch):
    from src.rag_service.routers import qa
    from src.rag_service.schemas.qa import (
        QAPairBatchCreateRequest, QAPairCreateRequest,
    )
    from src.rag_service.tasks import qa_processing
    from src.rag_service.services.qa_errors import QA_QUEUE_FAILURE

    session = MemorySession(None)
    session.execute = AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: None, fetchall=lambda: [],
    ))
    session.begin_nested = lambda: session

    def add(pair):
        pair.id = uuid4()
        session.documents.append(pair)

    session.add = add
    logger = Mock()
    monkeypatch.setattr(qa, 'logger', logger)
    monkeypatch.setattr(qa, 'validate_qa_collection', AsyncMock())
    monkeypatch.setattr(qa.QAPairResponse, 'model_validate', lambda value: value)
    task = (qa_processing.process_qa_pairs_batch_task if batch
            else qa_processing.process_qa_pair_task)
    monkeypatch.setattr(task, 'delay', Mock(
        side_effect=RuntimeError('redis://user:private-secret@host'),
    ))
    request = QAPairCreateRequest(question='测试问题', answer='测试答案')
    if batch:
        result = await qa.batch_create_qa_pairs(
            uuid4(), QAPairBatchCreateRequest(qa_pairs=[request]), uuid4(), session,
        )
        assert not result.success and result.failed_count == 1
        assert result.created_count == 1
        statement = session.execute.call_args.args[0]
        values = statement.compile().params
        assert values['status'] == 'failed'
        assert values['error_message'] == QA_QUEUE_FAILURE
    else:
        result = await qa.create_qa_pair(uuid4(), request, uuid4(), session)
        assert result.status == 'failed'
        assert result.error_message == QA_QUEUE_FAILURE
    assert 'private-secret' not in repr((result, logger.mock_calls))


@pytest.mark.asyncio
async def test_secondary_failure_logging_does_not_leak_credentials(
    fixture, monkeypatch,
):
    pair, session, embedding = fixture
    embedding.generate_embedding.side_effect = RuntimeError('HTTP 503 private-secret')
    session.commit = AsyncMock(side_effect=[None, RuntimeError('db private-secret')])
    logger = Mock()
    monkeypatch.setattr(tasks, 'logger', logger)
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert not result['success'] and 'HTTP 503' in result['error']
    assert logger.error.call_count == 2
    assert logger.error.call_args.kwargs['error_type'] == 'RuntimeError'
    assert 'private-secret' not in repr((result, logger.mock_calls))


@pytest.mark.asyncio
async def test_repeated_task_reuses_document(fixture):
    pair, session, _ = fixture
    await tasks.process_qa_pair_async(pair.id, pair.project_id)
    await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert len(session.documents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('vector', [[], [0.1], [float('nan')] * 1536, [0.0] * 1536])
async def test_invalid_embedding_never_publishes(fixture, vector):
    pair, session, embedding = fixture
    embedding.generate_embedding.return_value = vector
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert not result['success'] and pair.status == 'failed'
    assert not session.documents


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['edit', 'delete'])
async def test_changed_or_deleted_pair_during_embedding_cannot_be_published(
    fixture, change,
):
    from datetime import datetime, timezone

    pair, session, embedding = fixture

    async def changed(_):
        if change == 'edit':
            pair.answer = '新的答案'
            pair.status = 'pending'
        else:
            pair.deleted_at = datetime.now(timezone.utc)
        return [0.1] * 1536

    embedding.generate_embedding.side_effect = changed
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert result['skipped'] and not result['success']
    assert not session.documents


@pytest.mark.asyncio
async def test_wrong_project_cannot_process_or_modify_pair(fixture):
    pair, session, embedding = fixture
    result = await tasks.process_qa_pair_async(pair.id, uuid4())
    assert not result['success'] and pair.status == 'pending'
    embedding.generate_embedding.assert_not_awaited()
    assert not session.documents


@pytest.mark.asyncio
async def test_failure_of_old_content_does_not_replace_new_status(fixture):
    pair, _, embedding = fixture

    async def changed(_):
        pair.answer = '新的答案'
        pair.status = 'pending'
        raise RuntimeError('old task failed')

    embedding.generate_embedding.side_effect = changed
    await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert pair.status == 'pending' and pair.error_message is None


@pytest.mark.asyncio
async def test_success_clears_old_error_and_stores_embedding_metadata(fixture):
    pair, session, _ = fixture
    pair.error_message = 'earlier failure'
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    document = session.documents[0]
    assert result['success'] and pair.error_message is None
    assert document.embedding_dimensions == 1536
    assert document.embedding_model == 'fixture-model'
    assert document.content == tasks.build_qa_content(pair.question, pair.answer)


@pytest.mark.asyncio
async def test_delete_removes_text_vector_and_reference_together(fixture):
    from src.rag_service.routers import qa

    pair, session, _ = fixture
    await tasks.process_qa_pair_async(pair.id, pair.project_id)
    await qa.delete_qa_pair(pair.id, pair.project_id, session)
    assert pair.deleted_at is not None and pair.document_id is None
    assert not session.documents
    result = await tasks.process_qa_pair_async(pair.id, pair.project_id)
    assert not result['success'] and not session.documents


@pytest.mark.asyncio
@pytest.mark.parametrize('queue_error', [False, True])
async def test_failed_pair_can_retry_without_changing_answer(
    fixture, monkeypatch, queue_error,
):
    from src.rag_service.routers import qa
    from src.rag_service.schemas.qa import QAPairUpdateRequest
    from src.rag_service.tasks.qa_processing import process_qa_pair_task

    pair, session, _ = fixture
    pair.status = 'failed'
    pair.error_message = 'previous failure'
    enqueue = Mock(
        side_effect=RuntimeError('broker unavailable') if queue_error else None,
    )
    monkeypatch.setattr(process_qa_pair_task, 'delay', enqueue)
    monkeypatch.setattr(qa.QAPairResponse, 'model_validate', lambda pair: pair)
    await qa.update_qa_pair(
        pair.id, QAPairUpdateRequest(answer=pair.answer), pair.project_id, session,
    )
    enqueue.assert_called_once_with(str(pair.id), str(pair.project_id), True)
    assert pair.status == ('failed' if queue_error else 'pending')
    assert bool(pair.error_message) is queue_error


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [False, True])
async def test_only_content_edits_reset_existing_review(fixture, monkeypatch, changed):
    from src.rag_service.routers import qa
    from src.rag_service.schemas.qa import QAPairUpdateRequest
    from src.rag_service.services.knowledge_governance import KnowledgeGovernanceService
    from src.rag_service.tasks.qa_processing import process_qa_pair_task

    pair, session, _ = fixture
    pair.status = 'processed'
    invalidate = AsyncMock()
    monkeypatch.setattr(KnowledgeGovernanceService, 'invalidate_qa_review', invalidate)
    monkeypatch.setattr(process_qa_pair_task, 'delay', Mock())
    monkeypatch.setattr(qa.QAPairResponse, 'model_validate', lambda value: value)
    await qa.update_qa_pair(
        pair.id, QAPairUpdateRequest(answer='新答案' if changed else pair.answer),
        pair.project_id, session,
    )
    if changed:
        invalidate.assert_awaited_once_with(
            session, project_id=pair.project_id, qa_pair_id=pair.id,
        )
    else:
        invalidate.assert_not_awaited()
