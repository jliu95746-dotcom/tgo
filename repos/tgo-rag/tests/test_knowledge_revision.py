"""Resource revisions reflect evidence, rather than collection labels or counts."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.rag_service.services.knowledge_revision import collection_revision_statement


def test_revision_query_is_scoped_and_contains_current_governance_and_content():
    project = uuid4()
    statement = collection_revision_statement(project, "web", datetime.now(UTC))
    sql = str(statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    for required in (
        str(project), "web", "sha256", "rag_file_documents.content",
        "rag_knowledge_governance.review_status", "allow_automatic_reply",
        "effective_at", "expires_at", "rag_files.status", "rag_qa_pairs.answer",
        "rag_knowledge_version_sources.active_number", "rag_knowledge_versions.digest",
    ):
        assert required in sql
    assert "rag_file_documents.project_id" in sql
    assert "rag_knowledge_governance.project_id" in sql
    assert "rag_knowledge_version_sources.project_id" in sql
    assert "ORDER BY" in sql
