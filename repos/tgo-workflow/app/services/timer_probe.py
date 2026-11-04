"""Recent queue receipts in trusted Redis, scoped to one deployment config."""

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy.engine import make_url
from app.config import settings
from app.schemas.timer_runtime_health import TimerRuntimeState

PROBE_INTERVAL = 15
RECENT_SECONDS = 45
CHALLENGE_SECONDS = 30
RawProbe = str | bytes | None

ISSUE = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', 45)
redis.call('SET', KEYS[2], ARGV[2], 'EX', 30)
return 1
"""
ACKNOWLEDGE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('GET', KEYS[2]) ~= ARGV[2] then return 0 end
redis.call('SET', KEYS[3], ARGV[3], 'EX', 45)
redis.call('DEL', KEYS[2])
return 1
"""


class ProbeStore(Protocol):
    def get(self, key: str) -> RawProbe: ...
    def time(self) -> tuple[int, int]: ...
    def eval(self, script: str, count: int, *args: str | bytes) -> int: ...


class ProbeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: UUID
    probe_id: UUID


class TimerProbe(ProbeMessage):
    role: Literal["beat", "challenge", "worker"]
    observed_at: int = Field(ge=0, strict=True)
    database_ready: StrictBool | None = None

    @model_validator(mode="after")
    def check_result(self) -> "TimerProbe":
        if (self.role == "worker") != (self.database_ready is not None):
            raise ValueError("Probe outcome does not match its role")
        return self


def probe_scope() -> str | None:
    secret = settings.SECRET_KEY
    if secret is None or not secret.get_secret_value():
        return None
    target = make_url(settings.DATABASE_URL).set(password=None)
    context = json.dumps([
        "timer-runtime-v1", target.render_as_string(hide_password=True),
        settings.WORKFLOW_QUEUE_NAME, settings.API_INTERNAL_SERVICE_URL,
    ], separators=(",", ":"))
    return hmac.new(
        secret.get_secret_value().encode(), context.encode(), hashlib.sha256,
    ).hexdigest()


def probe_key(scope: str, kind: str) -> str:
    return "tgo:workflow:timer-health:v1:" + scope + ":" + kind


def decode_probe(raw: RawProbe, role: str) -> TimerProbe | None:
    if raw is None or len(raw) > 2048:
        return None
    try:
        result = TimerProbe.model_validate_json(raw)
        return result if result.role == role else None
    except ValueError:
        return None


def recent(probe: TimerProbe, now: int, seconds: int = RECENT_SECONDS) -> bool:
    return 0 <= now - probe.observed_at < seconds


def assess_runtime(
    beat_raw: RawProbe, worker_raw: RawProbe, *, now: int,
) -> TimerRuntimeState:
    result = TimerRuntimeState(
        status="scheduler_missing", checked_at=datetime.fromtimestamp(
            now, timezone.utc
        ),
    )
    if beat_raw is None:
        return result
    beat = decode_probe(beat_raw, "beat")
    if beat is None or beat.observed_at > now:
        return result.model_copy(update={"status": "invalid_probe"})
    if not recent(beat, now):
        return result
    result.scheduler_recent = True
    result.scheduler_seen_at = datetime.fromtimestamp(
        beat.observed_at, timezone.utc
    )
    result.status = "worker_waiting"
    if worker_raw is None:
        return result
    worker = decode_probe(worker_raw, "worker")
    if worker is None or worker.observed_at > now:
        return result.model_copy(update={"status": "invalid_probe"})
    if worker.instance_id != beat.instance_id or not recent(worker, now):
        return result
    result.worker_recent = True
    result.worker_seen_at = datetime.fromtimestamp(
        worker.observed_at, timezone.utc
    )
    result.database_ready = worker.database_ready is True
    result.ready = result.database_ready
    result.status = "ready" if result.ready else "database_unavailable"
    return result


def issue_probe(
    store: ProbeStore, scope: str, instance: UUID,
    publish: Callable[[ProbeMessage], None],
) -> None:
    probe = TimerProbe(
        instance_id=instance, probe_id=uuid4(), role="beat",
        observed_at=store.time()[0],
    )
    challenge = probe.model_copy(update={"role": "challenge"})
    stored = store.eval(
        ISSUE, 2, probe_key(scope, "beat"),
        probe_key(scope, "challenge:" + probe.probe_id.hex),
        probe.model_dump_json(), challenge.model_dump_json(),
    )
    if stored == 1:
        publish(ProbeMessage(
            instance_id=probe.instance_id, probe_id=probe.probe_id,
        ))


def consume_probe(
    store: ProbeStore, scope: str, message: ProbeMessage,
    check_database: Callable[[], bool],
) -> bool:
    beat_key = probe_key(scope, "beat")
    challenge_key = probe_key(scope, "challenge:" + message.probe_id.hex)
    raw = store.get(challenge_key)
    challenge = decode_probe(raw, "challenge")
    beat = decode_probe(store.get(beat_key), "beat")
    now = store.time()[0]
    if (challenge is None or beat is None
            or challenge.probe_id != message.probe_id
            or challenge.instance_id != message.instance_id
            or beat.instance_id != message.instance_id
            or not recent(challenge, now, CHALLENGE_SECONDS)
            or not recent(beat, now)):
        return False
    try:
        database_ready = check_database() is True
    except Exception:
        database_ready = False
    current_raw = store.get(beat_key)
    current = decode_probe(current_raw, "beat")
    now = store.time()[0]
    if (current is None or current.instance_id != message.instance_id
            or not recent(current, now)
            or not recent(challenge, now, CHALLENGE_SECONDS)):
        return False
    receipt = TimerProbe(
        role="worker", instance_id=message.instance_id,
        probe_id=message.probe_id, observed_at=now,
        database_ready=database_ready,
    )
    assert raw is not None and current_raw is not None
    return store.eval(
        ACKNOWLEDGE, 3, beat_key, challenge_key, probe_key(scope, "worker"),
        current_raw, raw, receipt.model_dump_json(),
    ) == 1
