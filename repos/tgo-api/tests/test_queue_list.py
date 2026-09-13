"""Queue list uses its existing items/pagination contract without phantom data."""

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.visitor_waiting_queue import list_waiting_queue
from app.models import Staff
from tests.test_queue_lifecycle import queue_db, seed_queue  # noqa: F401


@pytest.mark.asyncio
async def test_nonempty_list_is_typed_and_project_scoped(queue_db):
    Staff.__table__.create(queue_db.get_bind())
    visitor, session, entry = seed_queue(queue_db)
    seed_queue(queue_db)
    result = await list_waiting_queue(
        status=None, source=None, urgency=None, visitor_id=None,
        limit=20, offset=0, db=queue_db,
        current_user=SimpleNamespace(project_id=visitor.project_id),
    )
    assert result.pagination.total == 1
    assert len(result.items) == 1 and result.items[0].id == entry.id
    assert result.items[0].retry_count is None
    assert set(result.model_dump()) == {"items", "pagination"}
