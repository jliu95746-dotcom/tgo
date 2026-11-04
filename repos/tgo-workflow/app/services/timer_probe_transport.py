"""Dedicated bounded probe connections do not replace business dispatch."""

import asyncio
from typing import cast
from uuid import UUID
from celery import Celery
from redis import Redis
from app.config import settings
from app.services.timer_probe import (
    ProbeMessage, ProbeStore, consume_probe, issue_probe, probe_scope,
)
from app.services.timer_runtime_health import timer_database_ready


def publish_timer_probe(app: Celery, instance: UUID) -> None:
    if not settings.TIMER_EXECUTION_ENABLED:
        return
    scope = probe_scope()
    if scope is None:
        return

    def publish(message: ProbeMessage) -> None:
        # Do not inherit an unbounded broker socket or retry loop from tasks.
        with app.connection_for_write(
            connect_timeout=0.5,
            transport_options={
                "socket_connect_timeout": 0.5, "socket_timeout": 0.5,
                "retry_on_timeout": False,
            },
        ) as connection:
            app.send_task(
                "workflow_timer_runtime_probe",
                args=[message.model_dump(mode="json")],
                task_id="timer-probe:" + message.probe_id.hex,
                queue=settings.WORKFLOW_QUEUE_NAME,
                connection=connection, retry=False, ignore_result=True,
            )

    with Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5,
    ) as client:
        issue_probe(cast(ProbeStore, client), scope, instance, publish)


def consume_timer_probe(message: ProbeMessage) -> bool:
    if not settings.TIMER_EXECUTION_ENABLED:
        return False
    scope = probe_scope()
    if scope is None:
        return False
    with Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5,
    ) as client:
        return consume_probe(
            cast(ProbeStore, client), scope, message,
            lambda: asyncio.run(timer_database_ready()),
        )
