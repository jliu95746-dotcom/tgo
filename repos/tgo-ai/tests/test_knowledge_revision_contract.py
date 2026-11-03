"""Upstream knowledge evidence revisions remain typed and never imply approval."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.knowledge_availability import KnowledgeAvailability


@pytest.mark.parametrize("revision", [None, "b" * 64])
def test_revision_round_trip_and_older_owner_compatibility(revision):
    row = {"id": uuid4(), "name": "业务资料", "total_chunk_count": 1,
           "eligible_chunk_count": 1, "blocked_reasons": []}
    if revision:
        row["resource_revision"] = revision
    result = KnowledgeAvailability.model_validate({
        "project_id": uuid4(), "channel": "web", "collections": [row],
    })
    assert result.collections[0].resource_revision == revision
    assert "can_enable" not in result.model_dump()


@pytest.mark.parametrize("revision", ["", "a" * 63, "A" * 64, "untrusted-revision"])
def test_malformed_revision_is_rejected(revision):
    with pytest.raises(ValidationError):
        KnowledgeAvailability.model_validate({
            "project_id": uuid4(), "channel": "web", "collections": [{
                "id": uuid4(), "name": "资料", "total_chunk_count": 1,
                "eligible_chunk_count": 1, "resource_revision": revision,
            }],
        })
