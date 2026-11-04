"""Transactional turn collection and publication fencing, independent of HTTP tasks."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.customer_turn import CustomerTurn, CustomerTurnPart
from app.schemas.chat import ChatCompletionRequest
from app.schemas.customer_turn import TurnReceipt, TurnSuperseded

OPEN_STATES = ("collecting", "processing", "ready")
QUIET_SECONDS = 2
MAX_COLLECT_SECONDS = 5


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def actor_key(project: UUID, platform: UUID, channel: str, sender: str) -> str:
    return hashlib.sha256(
        f"{project}:{platform}:{channel}:{sender}".encode()
    ).hexdigest()


def receipt(turn: CustomerTurn, source: str, duplicate: bool = False) -> TurnReceipt:
    return TurnReceipt(
        turn_id=turn.id,
        version=turn.version,
        client_msg_no=turn.client_msg_no,
        source_message_id=source,
        duplicate=duplicate,
    )


class TurnStore:
    def __init__(self, db: Session):
        self.db = db

    def lock_actor(self, key: str) -> None:
        if self.db.get_bind().dialect.name == "postgresql":
            number = int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True)
            self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})

    def get(self, turn_id: UUID, *, lock: bool = False) -> CustomerTurn:
        query = (
            select(CustomerTurn)
            .where(CustomerTurn.id == turn_id)
            .execution_options(populate_existing=True)
        )
        row = self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "未找到本轮消息")
        return row

    def parts(self, turn_id: UUID) -> list[CustomerTurnPart]:
        return list(
            self.db.scalars(
                select(CustomerTurnPart)
                .where(CustomerTurnPart.turn_id == turn_id)
                .order_by(CustomerTurnPart.position)
            )
        )

    def ingest(
        self,
        *,
        project: UUID,
        platform: UUID,
        visitor: UUID,
        channel: str,
        sender: str,
        request: ChatCompletionRequest,
        can_reply: bool,
        external: bool,
        now: datetime | None = None,
        conversation: str | None = None,
    ) -> tuple[TurnReceipt, str | None]:
        now = now or datetime.now(timezone.utc)
        source = request.source_message_id
        if not source:
            raise HTTPException(422, "消息缺少稳定来源编号")
        key = actor_key(
            project,
            platform,
            channel + (":" + conversation if conversation else ""),
            sender,
        )
        self.lock_actor(key)
        payload = request.model_dump(mode="json", exclude={"api_key"})
        extra = payload.get("extra")
        if isinstance(extra, dict):
            allowed = {
                "source",
                "message_id",
                "wechat_local_group",
                "wechat_local_author",
                "wechat_local_mentioned",
                "wechat_local_mention_ambiguous",
                "reception_conversation",
                "reception_author",
            }
            payload["extra"] = {
                key: value for key, value in extra.items() if key in allowed
            }
        stable = {
            name: payload.get(name)
            for name in (
                "from_uid",
                "message",
                "msg_type",
                "media_pending",
                "media_error",
                "media_file_id",
                "extra",
            )
        }
        fingerprint = hashlib.sha256(
            json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        existing = self.db.scalar(
            select(CustomerTurnPart)
            .where(
                CustomerTurnPart.platform_id == platform,
                CustomerTurnPart.source_message_id == source,
            )
            .with_for_update()
        )
        previous_run = None
        turn: CustomerTurn | None
        if existing is not None:
            turn = self.get(existing.turn_id, lock=True)
            if turn.visitor_id != visitor or turn.actor_key != key:
                raise HTTPException(403, "消息来源不属于当前客户")
            if existing.fingerprint == fingerprint:
                return receipt(turn, source, True), None
            if (
                not existing.media_pending
                or request.media_pending
                or turn.state not in OPEN_STATES
            ):
                raise HTTPException(409, "同一来源编号不能替换消息内容")
            old = existing.payload
            if old.get("msg_type") != payload.get("msg_type") or old.get(
                "from_uid"
            ) != payload.get("from_uid"):
                raise HTTPException(409, "媒体身份与原消息不一致")
            existing.payload, existing.fingerprint = payload, fingerprint
            existing.media_pending = False
            previous_run = turn.client_msg_no
            if turn.state != "collecting":
                turn.collect_started_at = now
            turn.version += 1
        else:
            turn = self.db.scalar(
                select(CustomerTurn)
                .where(
                    CustomerTurn.actor_key == key, CustomerTurn.state.in_(OPEN_STATES)
                )
                .order_by(CustomerTurn.created_at.desc())
                .with_for_update()
                .limit(1)
            )
            if turn is not None:
                previous_run = turn.client_msg_no
                if turn.state != "collecting":
                    turn.collect_started_at = now
                turn.version += 1
            else:
                uncertain = self.db.scalar(
                    select(CustomerTurn.id)
                    .where(
                        CustomerTurn.actor_key == key, CustomerTurn.state == "uncertain"
                    )
                    .limit(1)
                )
                if uncertain:
                    raise HTTPException(409, "上次发送结果待核对，暂缓自动回复")
                turn = CustomerTurn(
                    id=uuid4(),
                    project_id=project,
                    platform_id=platform,
                    visitor_id=visitor,
                    channel_id=channel,
                    channel_type=251,
                    actor_key=key,
                    version=1,
                    state="collecting",
                    can_reply=False,
                    external_delivery=external,
                    created_at=now,
                    collect_started_at=now,
                    updated_at=now,
                    due_at=now,
                    client_msg_no="",
                )
                self.db.add(turn)
                self.db.flush()
            position = (
                self.db.scalar(
                    select(func.max(CustomerTurnPart.position)).where(
                        CustomerTurnPart.turn_id == turn.id
                    )
                )
                or 0
            ) + 1
            self.db.add(
                CustomerTurnPart(
                    turn_id=turn.id,
                    platform_id=platform,
                    source_message_id=source,
                    fingerprint=fingerprint,
                    payload=payload,
                    position=position,
                    media_pending=request.media_pending,
                    created_at=now,
                )
            )
        assert turn is not None
        self.release_permit(turn)
        turn.client_msg_no = "ai_" + uuid4().hex
        turn.state, turn.reply_text, turn.failure = "collecting", None, None
        turn.can_reply = turn.can_reply or can_reply
        turn.updated_at = now
        turn.due_at = min(
            now + timedelta(seconds=QUIET_SECONDS),
            utc(turn.collect_started_at) + timedelta(seconds=MAX_COLLECT_SECONDS),
        )
        self.db.flush()
        return receipt(turn, source), previous_run

    def current(
        self, turn_id: UUID, version: int, *, lock: bool = False
    ) -> CustomerTurn:
        turn = self.get(turn_id, lock=lock)
        if turn.version != version or turn.state not in OPEN_STATES:
            raise TurnSuperseded()
        return turn

    def release_permit(self, turn: CustomerTurn) -> None:
        if turn.permit:
            from app.services import ai_usage

            ai_usage.release(
                self.db,
                turn.project_id,
                UUID(turn.permit["reservation_id"]),
                UUID(turn.permit["lease_id"]),
                delivery_rejected=True,
            )
            turn.permit = None

    def stage(
        self,
        turn_id: UUID,
        version: int,
        reply: str,
        sender: str,
        permit: dict[str, str] | None,
        *,
        owner: UUID | None = None,
    ) -> CustomerTurn:
        turn = self.current(turn_id, version, lock=True)
        if owner is not None and (
            turn.lease_owner != owner
            or turn.lease_until is None
            or utc(turn.lease_until) <= datetime.now(timezone.utc)
        ):
            raise TurnSuperseded()
        turn.state, turn.reply_text, turn.from_uid = "ready", reply, sender
        turn.ready_at = datetime.now(timezone.utc)
        turn.due_at = turn.ready_at
        turn.permit = permit
        self.db.flush()
        return turn

    def claim(self, turn_id: UUID, version: int) -> CustomerTurn:
        turn = self.get(turn_id, lock=True)
        if turn.version != version:
            raise HTTPException(409, "本轮已有补充消息，旧答复已停止发送")
        if turn.state != "ready":
            raise HTTPException(409, "回复尚未就绪或已进入发送流程")
        if turn.permit:
            from app.services import ai_usage

            ai_usage.begin_publication(
                self.db,
                turn.project_id,
                UUID(turn.permit["reservation_id"]),
                UUID(turn.permit["lease_id"]),
                {"kind": "customer_turn", "turn_id": str(turn.id)},
            )
        turn.state, turn.claim_id = "sending", uuid4()
        turn.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return turn

    def confirm(
        self,
        turn_id: UUID,
        version: int,
        claim_id: UUID,
        result: str,
        proof: str | None,
    ) -> CustomerTurn:
        turn = self.get(turn_id, lock=True)
        if turn.version != version or turn.claim_id != claim_id:
            raise HTTPException(409, "发送回执不属于当前轮次")
        if turn.state in ("sent", "failed"):
            if turn.state == "sent" and result == "sent" and turn.receipt == proof:
                turn.failure = None
                return turn
            raise HTTPException(409, "发送结果已保存，不能覆盖")
        if turn.state == "uncertain" and result == "unknown":
            return turn
        if turn.state not in {"sending", "uncertain"} or result == "sent" and not proof:
            raise HTTPException(409, "缺少可核对的发送回执")
        turn.state = {"sent": "sent", "rejected": "failed", "unknown": "uncertain"}[
            result
        ]
        turn.receipt = proof
        turn.updated_at = datetime.now(timezone.utc)
        turn.failure = (
            None
            if result == "sent"
            else "发送结果待核对，停止自动重试"
            if result == "unknown"
            else "渠道未接受本轮回复"
        )
        turn.delivered_at = datetime.now(timezone.utc) if result == "sent" else None
        turn.due_at = datetime.now(timezone.utc)
        if turn.permit:
            from app.services import ai_usage

            if result == "sent":
                ai_usage.settle(
                    self.db,
                    turn.project_id,
                    UUID(turn.permit["reservation_id"]),
                    UUID(turn.permit["lease_id"]),
                )
            elif result == "rejected":
                self.release_permit(turn)
        self.db.flush()
        return turn
