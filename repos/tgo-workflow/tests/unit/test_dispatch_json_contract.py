"""Actual Celery JSON messages keep task arguments and idempotency IDs."""

from types import SimpleNamespace
from uuid import uuid4

from celery import Celery
import pytest

from app.services import workflow_dispatch
from celery_app import tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["initial", "pending", "approval"])
async def test_actual_json_message_keeps_arguments(monkeypatch, mode):
    queue = "synthetic-dispatch-" + uuid4().hex
    app = Celery("synthetic-dispatch", broker="memory://")
    app.conf.update(task_default_queue=queue, task_serializer="json")

    @app.task(name="synthetic.execute_workflow")
    def task(*args, **kwargs):
        raise AssertionError(
            "No worker or business action is part of this test"
        )

    monkeypatch.setattr(tasks, "execute_workflow_task", task)
    inputs = {"question": "synthetic request", "count": 0, "enabled": False}
    execution = SimpleNamespace(
        id=str(uuid4()),
        workflow_id=str(uuid4()),
        project_id=str(uuid4()),
        input=inputs,
        source_channel="web",
    )
    approval = SimpleNamespace(id=str(uuid4()))
    try:
        if mode == "initial":
            await workflow_dispatch.enqueue_workflow(
                object(), execution, inputs
            )
        elif mode == "pending":
            await workflow_dispatch.enqueue_pending_initial(execution)
        else:
            await workflow_dispatch.enqueue_approved_resume(
                execution, approval
            )
        with app.connection_for_read() as connection:
            with connection.SimpleQueue(queue) as messages:
                message = messages.get(block=False)
                assert message.content_type == "application/json"
                assert message.payload[0] == [
                    execution.id,
                    execution.workflow_id,
                    inputs,
                ]
                assert message.payload[1] == {
                    "project_id": execution.project_id
                }
                assert message.headers["id"] == (
                    f"{execution.id}:approval:{approval.id}"
                    if mode == "approval"
                    else execution.id
                )
                message.ack()
    finally:
        app.close()
