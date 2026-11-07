"""Check a displayed AI reply against tenant-scoped runs and persisted IM."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.ai_interaction_run import AIInteractionRun
from app.schemas.ai_runs import AIReplyOriginEvidence, AIReplyOriginStatus
from app.schemas.wukongim import WuKongIMMessage
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE


class AIReplyIMLookup(Protocol):
    enabled: bool

    async def get_message_by_client_msg_no(
        self,
        *,
        channel_id: str,
        channel_type: int,
        client_msg_no: str,
        raise_on_error: bool,
    ) -> WuKongIMMessage | None:
        ...


def _matches_anchor(
    message: WuKongIMMessage, channel_id: str, client_no: str
) -> bool:
    payload = message.payload
    return (
        message.channel_id == channel_id
        and message.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE
        and message.client_msg_no == client_no
        and message.from_uid.endswith("-staff")
        and message.message_id > 0
        and message.message_seq > 0
        and isinstance(payload, dict)
        and payload.get("type") == 100
        and payload.get("sender_kind") == "ai"
    )


async def verify_ai_reply_origins(
    db: Session,
    project_id: UUID,
    channel_id: str,
    client_msg_nos: list[str],
    im_client: AIReplyIMLookup,
) -> list[AIReplyOriginEvidence]:
    (
        'Verify current and retried AI replies without trusting a client '
        'marker.'
    )
    unique_nos = list(dict.fromkeys(client_msg_nos))
    current_nos = set(
        db.scalars(
            select(AIInteractionRun.response_client_msg_no).where(
                AIInteractionRun.project_id == project_id,
                AIInteractionRun.channel_id == channel_id,
                AIInteractionRun.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
                AIInteractionRun.response_client_msg_no.in_(unique_nos),
            )
        ).all()
    )
    attempted_nos = set(
        db.scalars(
            select(AIExecutionAttempt.response_client_msg_no)
            .join(
                AIInteractionRun,
                AIExecutionAttempt.interaction_run_id == AIInteractionRun.id,
            )
            .where(
                AIExecutionAttempt.project_id == project_id,
                AIInteractionRun.project_id == project_id,
                AIInteractionRun.channel_id == channel_id,
                AIInteractionRun.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
                AIExecutionAttempt.response_client_msg_no.in_(unique_nos),
            )
        ).all()
    )
    known_nos = current_nos | attempted_nos
    statuses: dict[str, AIReplyOriginStatus] = {}
    for client_no in unique_nos:
        if client_no not in known_nos:
            statuses[client_no] = "unrecorded"
            continue
        if not im_client.enabled:
            statuses[client_no] = "unavailable"
            continue
        try:
            message = await im_client.get_message_by_client_msg_no(
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                client_msg_no=client_no,
                raise_on_error=True,
            )
        except Exception:
            statuses[client_no] = "unavailable"
            continue
        statuses[client_no] = (
            "missing"
            if message is None
            else "verified"
            if _matches_anchor(message, channel_id, client_no)
            else "mismatch"
        )
    return [
        AIReplyOriginEvidence(
            client_msg_no=client_no, status=statuses[client_no]
        )
        for client_no in client_msg_nos
    ]
