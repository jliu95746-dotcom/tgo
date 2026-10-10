"""Internal, server-owned evidence for a single business answer."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    collection_id: str
    document_id: str
    file_id: str | None = None
    content: str = Field(min_length=1)
    relevance_score: float = 0.0


class BusinessToolEvidence(BaseModel):
    name: str
    content: str
    success: bool


class KnowledgeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "matched", "no_match", "unavailable", "over_budget", "skipped"
    ]
    retrieved_at: datetime
    project_id: str
    channel: str | None
    documents: list[KnowledgeDocument] = Field(
        default_factory=list, max_length=4
    )
    tool_results: list[BusinessToolEvidence] = Field(default_factory=list)

    def factual_text(self) -> str:
        return "\n".join(
            [
                *(
                    doc.content
                    for doc in self.documents
                    if self.status == "matched"
                ),
                *(tool.content for tool in self.tool_results if tool.success),
            ]
        )
