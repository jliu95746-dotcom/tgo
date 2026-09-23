"""Unclaimed queues expose only display identity and waiting time, never history."""

from types import SimpleNamespace

from app.services.company_queue import queue_previews
from tests.test_queue_lifecycle import queue_db, seed_queue  # noqa: F401


def test_waiting_preview_has_no_messages_contacts_or_metadata(queue_db):
    visitor, _, entry = seed_queue(queue_db)
    visitor.phone_number = "synthetic-phone"
    visitor.email = "private@example.com"
    entry.visitor_message = "private message must not be exposed"
    entry.extra_metadata = {"secret": "private metadata"}
    queue_db.commit()
    actor = SimpleNamespace(project_id=visitor.project_id)
    conversations, channels, total = queue_previews(queue_db, actor, 20, 0)
    assert total == 1
    assert conversations[0].recents == []
    assert channels[0].extra == {"queue_summary": True, "service_status": "queued"}
    serialized = channels[0].model_dump_json() + conversations[0].model_dump_json()
    assert "private" not in serialized and "synthetic-phone" not in serialized


def test_queue_preview_cannot_list_other_company(queue_db):
    visitor, _, _ = seed_queue(queue_db)
    other, _, _ = seed_queue(queue_db)
    conversations, _, total = queue_previews(queue_db, SimpleNamespace(project_id=visitor.project_id), 20, 0)
    assert total == 1 and str(other.id) not in conversations[0].channel_id
