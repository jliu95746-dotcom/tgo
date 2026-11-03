"""Bounded, process-local callback latency histogram for Prometheus scraping."""

from __future__ import annotations

from collections import Counter, defaultdict
from threading import RLock


CALLBACK_BUCKETS_SECONDS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0)
OBSERVED_CHANNELS = frozenset(
    {"wecom", "wecom_bot", "website", "feishu_bot", "dingtalk_bot", "telegram"}
)


class CallbackMetrics:
    """Track callback durations without request identifiers or message content."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str]] = Counter()
        self._sums: defaultdict[tuple[str, str], float] = defaultdict(float)
        self._buckets: Counter[tuple[str, str, float]] = Counter()
        self._lock = RLock()

    def observe(
        self, *, channel: str, status_code: int, duration_seconds: float
    ) -> None:
        safe_channel = channel if channel in OBSERVED_CHANNELS else "other"
        if 200 <= status_code < 400:
            outcome = "success"
        elif 400 <= status_code < 500:
            outcome = "client_error"
        else:
            outcome = "server_error"
        duration = max(0.0, duration_seconds)
        key = (safe_channel, outcome)
        with self._lock:
            self._counts[key] += 1
            self._sums[key] += duration
            for bucket in CALLBACK_BUCKETS_SECONDS:
                if duration <= bucket:
                    self._buckets[(safe_channel, outcome, bucket)] += 1

    def render_prometheus(self) -> str:
        """Return a scrape snapshot in the Prometheus text format."""
        lines = [
            "# HELP tgo_platform_callback_duration_seconds Callback HTTP response time.",
            "# TYPE tgo_platform_callback_duration_seconds histogram",
        ]
        with self._lock:
            for (channel, outcome), count in sorted(self._counts.items()):
                labels = f'channel="{channel}",outcome="{outcome}"'
                for bucket in CALLBACK_BUCKETS_SECONDS:
                    bucket_label = format(bucket, "g")
                    value = self._buckets[(channel, outcome, bucket)]
                    lines.append(
                        "tgo_platform_callback_duration_seconds_bucket"
                        f'{{{labels},le="{bucket_label}"}} {value}'
                    )
                lines.append(
                    "tgo_platform_callback_duration_seconds_bucket"
                    f'{{{labels},le="+Inf"}} {count}'
                )
                lines.append(
                    f"tgo_platform_callback_duration_seconds_sum{{{labels}}} "
                    f"{self._sums[(channel, outcome)]:.6f}"
                )
                lines.append(
                    f"tgo_platform_callback_duration_seconds_count{{{labels}}} {count}"
                )
        return "\n".join(lines) + "\n"
