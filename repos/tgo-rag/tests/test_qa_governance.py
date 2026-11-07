"""QA governance has the same tenant boundary and review lifecycle as files."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.rag_service.models.qa import QAPair
from src.rag_service.schemas.knowledge_governance import KnowledgeGovernanceInput
from src.rag_service.services.knowledge_governance import (
    KnowledgeGovernanceNotFoundError, KnowledgeGovernanceService,
)


def test_qa_record_response_uses_question_as_source_name():
    project, pair_id, collection = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    record = KnowledgeGovernanceService.new_record(project, KnowledgeGovernanceInput(
        qa_pair_id=pair_id, document_type='faq', product_line='验收产品',
        channels=['web'], effective_at=now, owner='测试', document_version='v1',
    ))
    record.id, record.created_at, record.updated_at = uuid4(), now, now
    pair = QAPair(id=pair_id, project_id=project, collection_id=collection,
                  question='保修多久？', answer='一年')
    response = KnowledgeGovernanceService._record_response(record, pair)
    assert response.source_name == pair.question
    assert response.qa_pair_id == pair_id
    assert response.file_id is None
    assert response.collection_id == collection


@pytest.mark.asyncio
async def test_qa_lookup_rejects_missing_or_other_tenant_source():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute.return_value = result
    with pytest.raises(KnowledgeGovernanceNotFoundError):
        await KnowledgeGovernanceService._get_qa_pair(
            db, project_id=uuid4(), qa_pair_id=uuid4(),
        )
    sql = str(db.execute.call_args.args[0])
    assert 'rag_qa_pairs.project_id =' in sql
    assert 'rag_qa_pairs.deleted_at IS NULL' in sql


@pytest.mark.asyncio
async def test_changed_qa_invalidates_review_without_enabling_reply():
    db = AsyncMock()
    await KnowledgeGovernanceService.invalidate_qa_review(
        db, project_id=uuid4(), qa_pair_id=uuid4(),
    )
    statement = db.execute.call_args.args[0]
    sql = str(statement)
    values = statement.compile().params
    assert 'project_id =' in sql and 'qa_pair_id =' in sql
    assert values['review_status'] == 'draft'
    assert values['reviewed_by'] is None and values['reviewed_at'] is None
    assert 'allow_automatic_reply' not in values
    db.commit.assert_not_called()
