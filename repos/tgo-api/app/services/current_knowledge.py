"""Validate the internal AI evidence envelope before customer publication."""
from datetime import datetime, timezone

from pydantic import ValidationError

from app.schemas.knowledge_evidence import KnowledgeEvidence

UNCONFIRMED_REPLY = "目前还无法确认这项信息。如需进一步核实，可以联系人工客服。"


def read_knowledge_evidence(
    value: object,
    *,
    project_id: str,
    channel: str | None,
) -> KnowledgeEvidence:
    try:
        evidence = KnowledgeEvidence.model_validate(value)
        if evidence.project_id == project_id and evidence.channel == channel:
            return evidence
    except ValidationError:
        pass
    # A missing or malformed envelope cannot make an old draft authoritative.
    return KnowledgeEvidence(
        status="unavailable",
        retrieved_at=datetime.now(timezone.utc),
        project_id=project_id,
        channel=channel,
    )
