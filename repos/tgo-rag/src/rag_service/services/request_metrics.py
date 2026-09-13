"""Process-scoped HTTP observations; bounded latency history and no user/path labels."""

import math
import time
from collections import deque
from collections.abc import Callable
from threading import Lock

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..schemas.observability import RequestMetricsValues


class RequestMetrics:
    def __init__(
        self,
        registry: CollectorRegistry,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.registry = registry
        self.clock = clock
        self.started_at = clock()
        self._lock = Lock()
        self._total = self._errors = self._active = 0
        self._latencies: deque[float] = deque(maxlen=1000)
        self._requests_counter = Counter(
            "tgo_rag_http_requests_total",
            "Completed HTTP requests excluding probes",
            registry=registry,
        )
        self._errors_counter = Counter(
            "tgo_rag_http_errors_total",
            "HTTP errors and incomplete responses",
            registry=registry,
        )
        self._active_gauge = Gauge(
            "tgo_rag_http_active_requests",
            "Active HTTP requests including streams",
            registry=registry,
        )
        self._duration = Histogram(
            "tgo_rag_http_duration_seconds",
            "HTTP response completion time",
            registry=registry,
        )

    def begin(self) -> float:
        with self._lock:
            self._active += 1
            self._active_gauge.inc()
        return self.clock()

    def finish(self, started: float, error: bool) -> None:
        elapsed = max(0.0, self.clock() - started)
        with self._lock:
            self._active -= 1
            self._total += 1
            self._errors += int(error)
            self._latencies.append(elapsed * 1000)
            self._active_gauge.dec()
            self._requests_counter.inc()
            self._errors_counter.inc(int(error))
            self._duration.observe(elapsed)

    def snapshot(self) -> RequestMetricsValues:
        with self._lock:
            uptime = max(0.0, self.clock() - self.started_at)
            values = sorted(self._latencies)
            p95 = values[math.ceil(len(values) * 0.95) - 1] if values else None
            return RequestMetricsValues(
                requests_total=self._total,
                requests_per_second=self._total / uptime if uptime else 0.0,
                response_time_p95=p95,
                latency_samples=len(values),
                active_requests=self._active,
                errors_total=self._errors,
                uptime_seconds=uptime,
            )


request_metrics = RequestMetrics(REGISTRY)
PROBE_PATHS = frozenset({"/health", "/ready", "/live", "/metrics", "/metrics/json"})


class RequestMetricsMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        metrics: RequestMetrics = request_metrics,
        enabled: bool = True,
    ) -> None:
        self.app, self.metrics, self.enabled = app, metrics, enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self.enabled
            or scope["type"] != "http"
            or scope.get("path", "").rstrip("/") in PROBE_PATHS
        ):
            await self.app(scope, receive, send)
            return
        started = self.metrics.begin()
        status, complete = 500, False

        async def observe_send(message: Message) -> None:
            nonlocal status, complete
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if (
                not complete
                and message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                complete = True
                self.metrics.finish(started, status >= 400)

        try:
            await self.app(scope, receive, observe_send)
        finally:
            if not complete:
                self.metrics.finish(started, True)
