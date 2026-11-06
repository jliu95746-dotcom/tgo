"""Private producer receipts for timeout retries; never trust model flags."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.schemas.ai_runs import ReplyRun
from app.services.timed_ai_fallback import FallbackSource
from app.utils.encoding import build_visitor_channel_id


@dataclass
class FallbackReplyAttempt:
    source: FallbackSource
    generation: UUID | None = None
    publication_started: bool = False
    anchor_started: bool = False
    failure_saved: bool = False
    upstream_closed: bool = False
    cancelled: bool = False
    control_failed: bool = False
    control_ended: bool = False

    def owns(self, identity: ReplyRun) -> bool:
        return (
            identity.project_id == str(self.source.project_id)
            and identity.client_msg_no == self.source.response_client_msg_no
            and identity.channel_type == 251
            and identity.channel_id
            == build_visitor_channel_id(self.source.visitor_id)
        )

    @property
    def retry_allowed(self) -> bool:
        return (
            self.generation is not None and self.control_ended
            and self.failure_saved and self.upstream_closed
            and not self.publication_started
            and not self.cancelled and not self.control_failed
        )

    @property
    def can_notify_failure(self) -> bool:
        return (
            self.generation is not None and self.control_ended
            and self.failure_saved
            and not self.publication_started
            and not self.cancelled and not self.control_failed
        )


_current: ContextVar[FallbackReplyAttempt | None] = ContextVar(
    "fallback_reply_attempt", default=None
)


@contextmanager
def attempt_scope(attempt: FallbackReplyAttempt) -> Iterator[None]:
    token = _current.set(attempt)
    try:
        yield
    finally:
        _current.reset(token)


def has_fallback_attempt() -> bool:
    return _current.get() is not None


def observe_control_start(identity: ReplyRun) -> None:
    attempt = _current.get()
    if attempt is not None:
        if attempt.owns(identity):
            attempt.generation = identity.generation
        else:
            attempt.control_failed = True


def observe_publication_start(identity: ReplyRun) -> None:
    attempt = _current.get()
    if attempt is not None:
        # Includes uncertain grant requests.
        attempt.publication_started = True
        if (
            not attempt.owns(identity)
            or attempt.generation != identity.generation
        ):
            attempt.control_failed = True


def observe_control_outcome(
    identity: ReplyRun, saved: ReplyRun | None, *, upstream_closed: bool,
    cancelled: bool, control_failed: bool,
) -> None:
    attempt = _current.get()
    if attempt is None:
        return
    if not attempt.owns(identity) or attempt.generation != identity.generation:
        attempt.control_failed = True
        return
    attempt.cancelled = attempt.cancelled or cancelled or (
        saved is not None and (
            saved.status == "cancelled"
            or saved.failure_reason == "upstream_stop_unconfirmed"
        )
    )
    attempt.control_failed = attempt.control_failed or control_failed
    attempt.upstream_closed = upstream_closed
    attempt.failure_saved = (
        saved is not None and saved.generation == identity.generation
        and saved.status == "failed"
        and saved.failure_reason not in {
            "control_unavailable", "upstream_stop_unconfirmed"
        }
    )


def observe_control_end(identity: ReplyRun, *, clean: bool) -> None:
    attempt = _current.get()
    if attempt is not None:
        attempt.control_ended = (
            clean and attempt.owns(identity)
            and attempt.generation == identity.generation
        )


def event_action(
    event_type: str, channel_id: str, channel_type: int,
    client_msg_no: str, from_uid: str,
) -> Literal["pass", "defer", "anchor"]:
    attempt = _current.get()
    if attempt is None:
        return "pass"
    source = attempt.source
    if (
        channel_id != build_visitor_channel_id(source.visitor_id)
        or channel_type != 251
        or client_msg_no != source.response_client_msg_no
        or from_uid != f"{source.staff_id}-staff"
    ):
        from app.services.ai_reply_control import ReplyStopped

        attempt.control_failed = True
        raise ReplyStopped("超时接管消息不属于原客户会话。")
    if not attempt.publication_started:
        if event_type in {"workflow_completed", "agent_response_complete"}:
            from app.services.ai_reply_control import ReplyStopped

            attempt.control_failed = True
            raise ReplyStopped("超时接管缺少发布确认。")
        return "defer"
    if event_type == "agent_execution_started":
        if attempt.anchor_started:
            return "defer"
        # An uncertain anchor must not be retried.
        attempt.anchor_started = True
    elif not attempt.anchor_started:
        return "anchor"
    return "pass"
