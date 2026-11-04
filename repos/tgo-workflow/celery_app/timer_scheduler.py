"""Pulse from the actual Beat loop, not from a worker impersonating Beat."""

import time
from uuid import UUID, uuid4
from celery.beat import PersistentScheduler
from app.config import settings
from app.core.logging import logger
from app.services.timer_probe import PROBE_INTERVAL
from app.services.timer_probe_transport import publish_timer_probe


class TimerHealthScheduler(PersistentScheduler):
    _next_timer_probe: float = 0
    _timer_instance: UUID | None = None

    def tick(self, **kwargs: object) -> float:
        if settings.TIMER_EXECUTION_ENABLED:
            now = time.monotonic()
            if now >= self._next_timer_probe:
                self._next_timer_probe = now + PROBE_INTERVAL
                if self._timer_instance is None:
                    self._timer_instance = uuid4()
                try:
                    publish_timer_probe(self.app, self._timer_instance)
                except Exception as error:
                    logger.warning(
                        "Timer scheduler probe failed: %s",
                        type(error).__name__,
                    )
        delay = float(super().tick(**kwargs))
        return min(delay, PROBE_INTERVAL) if (
            settings.TIMER_EXECUTION_ENABLED
        ) else delay
