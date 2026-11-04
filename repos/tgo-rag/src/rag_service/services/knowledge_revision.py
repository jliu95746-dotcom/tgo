"""Database-owned, content-aware revisions for governed knowledge collections.

Hash content in PostgreSQL; neither source text nor credentials leave the owner.
Counts and revisions share one statement snapshot and the retrieval admission rule.
"""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import String, Text, and_, cast as sql_cast, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression

from ..models import Collection, File, FileDocument, QAPair
from ..models.knowledge_governance import KnowledgeGovernanceRecord
from ..models.knowledge_versions import KnowledgeVersion, KnowledgeVersionSource
from ..schemas.knowledge_governance import KnowledgeChannel
from .knowledge_governance import KnowledgeGovernancePolicy


def _digest(*values: SQLColumnExpression[object]) -> ColumnElement[str]:
    return cast(ColumnElement[str], func.encode(func.sha256(func.convert_to(
        sql_cast(func.jsonb_build_array(*values), Text), "UTF8",
    )), "hex"))


def _text(value: SQLColumnExpression[object]) -> ColumnElement[str]:
    # SQL CAST changes the SQL result type; some legacy ORM annotations use
    # the SQL UUID type instead of Python UUID and cannot infer this conversion.
    return sql_cast(cast(SQLColumnExpression[str], value), Text)


def _row(
    kind: str, collection: SQLColumnExpression[object],
    identifier: SQLColumnExpression[object], *values: SQLColumnExpression[object],
) -> Select[tuple[UUID | None, str, str, str]]:
    return cast(Select[tuple[UUID | None, str, str, str]], select(
        collection.label("collection_id"), literal(kind).label("kind"),
        sql_cast(identifier, String).label("row_id"),
        _digest(literal(kind), identifier, *values).label("digest"),
    ))


def collection_counts_statement(
    project: UUID, channel: KnowledgeChannel | str, at: datetime,
) -> Select[tuple[UUID, int, int]]:
    admitted = KnowledgeGovernancePolicy.eligible_document_ids_statement(
        project_id=project, at=at, channel=KnowledgeChannel(channel),
    )
    return select(
        FileDocument.collection_id,
        func.count(FileDocument.id),
        func.count(FileDocument.id).filter(FileDocument.id.in_(admitted)),
    ).where(FileDocument.project_id == project).group_by(FileDocument.collection_id)


def collection_revision_statement(
    project: UUID, channel: KnowledgeChannel | str, at: datetime,
) -> Select[tuple[UUID, str, int, int, str]]:
    """One query per project, independent of the number of collections.

The clock itself is excluded. Admission changes at effective/expiry boundaries
change the revision; reads within the same validity interval stay stable.
Pending version drafts and display-only collection renames are excluded.
"""
    channel = KnowledgeChannel(channel)
    admitted = KnowledgeGovernancePolicy.eligible_document_ids_statement(
        project_id=project, at=at, channel=channel,
    )
    documents = _row(
        "document", FileDocument.collection_id, FileDocument.id,
        FileDocument.file_id, FileDocument.content, FileDocument.document_title,
        _text(FileDocument.content_tsv), FileDocument.section_title, FileDocument.tags,
        FileDocument.embedding_model, FileDocument.embedding_dimensions,
        _text(FileDocument.embedding), FileDocument.updated_at,
        FileDocument.id.in_(admitted),
    ).where(FileDocument.project_id == project)
    files = _row(
        "file", File.collection_id, File.id, File.status,
    ).where(File.project_id == project, File.deleted_at.is_(None))
    pairs = _row(
        "qa", QAPair.collection_id, QAPair.id,
        QAPair.question, QAPair.answer, QAPair.document_id, QAPair.status,
    ).where(QAPair.project_id == project, QAPair.deleted_at.is_(None))
    governance = KnowledgeGovernanceRecord
    source_collection = cast(ColumnElement[UUID], func.coalesce(
        File.collection_id, QAPair.collection_id,
    ))
    governance_rows = _row(
        "governance", source_collection, governance.id,
        governance.file_id, governance.qa_pair_id, governance.document_type,
        governance.product_line, governance.document_version,
        governance.channels, governance.allow_automatic_reply,
        governance.review_status, governance.reviewed_at, governance.source_origin,
        governance.effective_at, governance.expires_at,
        governance.effective_at <= at,
        or_(governance.expires_at.is_(None), governance.expires_at > at),
    ).select_from(governance).outerjoin(File, and_(
        governance.file_id == File.id, File.project_id == project,
        File.deleted_at.is_(None),
    )).outerjoin(QAPair, and_(
        governance.qa_pair_id == QAPair.id, QAPair.project_id == project,
        QAPair.deleted_at.is_(None),
    )).where(
        governance.project_id == project, governance.deleted_at.is_(None),
        source_collection.is_not(None),
    )
    source = KnowledgeVersionSource
    version = KnowledgeVersion
    active_versions = _row(
        "publication", source.collection_id, source.id,
        source.source_kind, source.source_id, source.active_file_id,
        source.active_number, source.disabled,
        version.digest, version.state,
    ).select_from(source).outerjoin(version, and_(
        version.source_id == source.id, version.number == source.active_number,
    )).where(source.project_id == project)
    evidence = union_all(documents, files, pairs, governance_rows, active_versions).subquery()
    revisions = select(
        evidence.c.collection_id,
        func.string_agg(evidence.c.digest, aggregate_order_by(
            literal("|"), evidence.c.kind, evidence.c.row_id,
        )).label("members"),
    ).group_by(evidence.c.collection_id).subquery()
    counts = collection_counts_statement(project, channel, at).subquery()
    total = func.coalesce(counts.c[1], 0)
    eligible = func.coalesce(counts.c[2], 0)
    statement = select(
        Collection.id, Collection.display_name, total, eligible,
        _digest(
            literal("knowledge-revision-v1"), Collection.project_id, Collection.id,
            literal(channel.value), _text(Collection.collection_type),
            Collection.collection_metadata,
            func.coalesce(revisions.c.members, ""), total, eligible,
        ).label("resource_revision"),
    ).outerjoin(revisions, revisions.c.collection_id == Collection.id).outerjoin(
        counts, counts.c[0] == Collection.id,
    ).where(
        Collection.project_id == project, Collection.deleted_at.is_(None),
    ).order_by(Collection.display_name, Collection.id)
    return cast(Select[tuple[UUID, str, int, int, str]], statement)
