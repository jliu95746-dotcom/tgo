"""Only an actual consumer of the configured queue can acknowledge a probe."""

from typing import TypedDict
from app.core.logging import logger
from app.services.timer_probe import ProbeMessage
from app.services.timer_probe_transport import consume_timer_probe
from celery_app.celery import celery_app


class ProbePayload(TypedDict):
    instance_id: str
    probe_id: str


@celery_app.task(name="workflow_timer_runtime_probe", ignore_result=True)
def workflow_timer_runtime_probe(message: ProbePayload) -> bool:
    try:
        return consume_timer_probe(ProbeMessage.model_validate(message))
    except Exception as error:
        logger.warning("Timer worker probe failed: %s", type(error).__name__)
        return False
