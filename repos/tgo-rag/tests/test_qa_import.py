"""Malformed imports fail before writes; QA uniqueness excludes tombstones."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.schema import CreateIndex
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from src.rag_service.models import QAPair
from src.rag_service.routers import qa
from src.rag_service.schemas.qa import (
    QAPairCreateRequest,
    QAPairImportRequest,
    QAPairUpdateRequest,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        "[null]",
        "[1]",
        '["question answer"]',
        '[{"question":"ok","answer":null}]',
        '[{"question":"   ","answer":"ok"}]',
        '[{"question":"ok","answer":"ok","priority":101}]',
        '[{"question":"ok","answer":"\\u0000"}]',
    ],
)
async def test_invalid_json_rows_return_400_without_any_batch_write(monkeypatch, data):
    monkeypatch.setattr(qa, "validate_qa_collection", AsyncMock())
    batch = AsyncMock()
    monkeypatch.setattr(qa, "batch_create_qa_pairs", batch)
    with pytest.raises(HTTPException) as failure:
        await qa.import_qa_pairs(
            uuid4(), QAPairImportRequest(data=data), uuid4(), AsyncMock()
        )
    assert failure.value.status_code == 400
    assert "1" in failure.value.detail
    batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_csv_bom_and_optional_blank_fields_are_normalized(monkeypatch):
    monkeypatch.setattr(qa, "validate_qa_collection", AsyncMock())
    batch = AsyncMock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(qa, "batch_create_qa_pairs", batch)
    await qa.import_qa_pairs(
        uuid4(),
        QAPairImportRequest(
            format="csv",
            data=('\ufeffquestion,answer,priority,tags\n'
                  ' question , answer ,,"one,two"\n'),
        ),
        uuid4(),
        AsyncMock(),
    )
    pair = batch.await_args.args[1].qa_pairs[0]
    assert pair.question == "question" and pair.answer == "answer"
    assert pair.priority == 0 and pair.tags == ["one", "two"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        "question,answer,answer\nquestion,first answer,second answer\n",
        "question,answer,tags,tags\nquestion,answer,first tag,second tag\n",
    ],
)
async def test_duplicate_csv_headers_fail_before_any_batch_write(monkeypatch, data):
    monkeypatch.setattr(qa, "validate_qa_collection", AsyncMock())
    batch = AsyncMock()
    monkeypatch.setattr(qa, "batch_create_qa_pairs", batch)
    with pytest.raises(HTTPException) as failure:
        await qa.import_qa_pairs(
            uuid4(),
            QAPairImportRequest(format="csv", data=data),
            uuid4(),
            AsyncMock(),
        )
    assert failure.value.status_code == 400
    assert "重复" in failure.value.detail
    batch.assert_not_awaited()


@pytest.mark.parametrize("schema", [QAPairCreateRequest, QAPairUpdateRequest])
def test_whitespace_and_nul_are_rejected_before_database(schema):
    with pytest.raises(ValidationError):
        schema(question=" \t ", answer="ok")
    with pytest.raises(ValidationError):
        schema(question="ok", answer="\x00")


def test_active_question_uniqueness_preserves_deleted_history():
    index = next(
        index
        for index in QAPair.__table__.indexes
        if index.name == "idx_qa_pairs_collection_question"
    )
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in ddl and "WHERE deleted_at IS NULL" in ddl


class BatchSession:
    def __init__(self):
        self.saved = []
        self.row = None
        self.aborted = False

    async def execute(self, _):
        return SimpleNamespace(fetchall=lambda: [])

    def begin_nested(self):
        session = self

        class Savepoint:
            async def __aenter__(self):
                assert not session.aborted

            async def __aexit__(self, *_):
                session.aborted = False

        return Savepoint()

    def add(self, row):
        self.row = row

    async def flush(self):
        assert not self.aborted
        if self.row.question in ("duplicate", "invalid"):
            self.aborted = True
            name = (
                "idx_qa_pairs_collection_question"
                if self.row.question == "duplicate"
                else "other_constraint"
            )
            raise IntegrityError("insert", {}, RuntimeError(name))
        self.row.id = uuid4()
        self.saved.append(self.row)

    async def commit(self):
        assert not self.aborted


@pytest.mark.asyncio
async def test_batch_savepoints_keep_good_rows_after_duplicate_and_rejected_row(
    monkeypatch,
):
    from src.rag_service.schemas.qa import QAPairBatchCreateRequest
    from src.rag_service.tasks.qa_processing import process_qa_pairs_batch_task
    from unittest.mock import Mock

    monkeypatch.setattr(qa, "validate_qa_collection", AsyncMock())
    enqueue = Mock()
    monkeypatch.setattr(process_qa_pairs_batch_task, "delay", enqueue)
    db = BatchSession()
    result = await qa.batch_create_qa_pairs(
        uuid4(),
        QAPairBatchCreateRequest(
            qa_pairs=[
                QAPairCreateRequest(question=question, answer="test")
                for question in ("good-first", "duplicate", "invalid", "good-last")
            ]
        ),
        uuid4(),
        db,
    )
    assert (
        result.created_count == 2
        and result.skipped_count == 1
        and result.failed_count == 1
    )
    assert [row.question for row in db.saved] == ["good-first", "good-last"]
    assert result.success is False
    assert len(enqueue.call_args.args[0]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "constraint", ["idx_qa_pairs_collection_question", "other_constraint"]
)
async def test_commit_duplicate_has_explicit_conflict_and_rollback(constraint):
    db = SimpleNamespace(
        commit=AsyncMock(
            side_effect=IntegrityError("insert", {}, RuntimeError(constraint))
        ),
        rollback=AsyncMock(),
    )
    expected = (
        HTTPException
        if constraint == "idx_qa_pairs_collection_question"
        else IntegrityError
    )
    with pytest.raises(expected) as error:
        await qa.commit_qa_changes(db)
    if isinstance(error.value, HTTPException):
        assert error.value.status_code == 400
    db.rollback.assert_awaited_once()
