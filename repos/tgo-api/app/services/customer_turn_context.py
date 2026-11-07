"Bind one generated version to all recognition, routing, and publication \
phases."

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import logging
import time
from uuid import UUID

from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.schemas.chat import ChatCompletionRequest
from app.schemas.chat_media import ChatMediaInput
from app.schemas.customer_turn import TurnSuperseded
from app.services.customer_turn_store import TurnStore


@dataclass
class TurnExecution:
    turn_id: UUID
    version: int
    project_id: UUID
    platform_id: UUID
    visitor_id: UUID
    external: bool
    staged: bool = False
    sender_uid: str = ""
    media_failure: str = ""
    expired_media: bool = False
    owner: UUID | None = None
    client_msg_no: str = ""
    isolated_history: bool = False
    media_input: dict[str, object] = field(default_factory=dict)
    generation_started: float = 0.0


current_execution: ContextVar[TurnExecution | None] = ContextVar(
    "customer_turn", default=None
)


async def ensure_current() -> None:
    context = current_execution.get()
    if context is None:
        return
    with SessionLocal() as db:
        turn = TurnStore(db).current(context.turn_id, context.version)
        if context.owner is not None:
            from datetime import datetime, timezone
            from app.services.customer_turn_store import utc

            if (
                turn.lease_owner != context.owner
                or turn.lease_until is None
                or utc(turn.lease_until) <= datetime.now(timezone.utc)
            ):
                raise TurnSuperseded()
        platform = db.get(Platform, context.platform_id)
        visitor = db.get(Visitor, context.visitor_id)
        from app.services.chat_service import is_ai_disabled

        if (
            platform is None
            or visitor is None
            or not platform.is_active
            or platform.deleted_at is not None
            or visitor.deleted_at is not None
            or visitor.platform_id != platform.id
            or is_ai_disabled(platform, visitor)
        ):
            raise TurnSuperseded()
        if platform.type == "wechat_local":
            from app.services.wechat_local_bridge import require_scoped_target
            from app.utils.wechat_local import is_filehelper_identity

            if not is_filehelper_identity(visitor.platform_open_id):
                target = await require_scoped_target(
                    platform, visitor.platform_open_id
                )
                if target.is_group:
                    permitted = False
                    for part in TurnStore(db).parts(turn.id):
                        payload = part.payload
                        extra = payload.get("extra")
                        extra = extra if isinstance(extra, dict) else {}
                        if extra.get("wechat_local_mention_ambiguous") is True:
                            continue
                        text = (
                            str(payload.get("message", ""))
                            if payload.get("msg_type") == 1
                            else ""
                        )
                        permitted |= target.allows_ai(
                            text, extra.get("wechat_local_mentioned") is True
                        )
                    if not permitted:
                        raise TurnSuperseded()


async def prepare_input() -> tuple[str, str]:
    context = current_execution.get()
    if context is None:
        raise RuntimeError("No customer turn")
    await ensure_current()
    with SessionLocal() as db:
        parts = [
            (part.source_message_id, dict(part.payload), part.media_pending)
            for part in TurnStore(db).parts(context.turn_id)
        ]
    context.media_input = {}
    started = time.monotonic()
    normalized: list[dict[str, str]] = []
    failures: list[str] = []
    understood = 0
    from app.services.chat_media_analysis import prepare_chat_media

    for source, payload, pending in parts:
        await ensure_current()
        request = ChatCompletionRequest.model_validate(
            {**payload, "api_key": "internal-turn"}
        )
        if request.msg_type in (2, 4):
            from app.services.chat_media_service import MediaInputError

            if pending and not context.expired_media:
                raise RuntimeError("Media is not ready")
            if request.media_error or pending:
                failures.append(
                    "图片未能识别，请在微信中打开图片后重发，或用文字描述问题。"
                    if request.msg_type == 2
                    else "语音未能识别，请重新发送语音或改用文字说明。"
                )
                normalized.append(
                    {
                        "kind": "image" if request.msg_type == 2 else "voice",
                        "content": "本条媒体未识别，不能推测其中内容。",
                    }
                )
                continue
            try:
                prepared = await prepare_chat_media(
                    ChatMediaInput(
                        project_id=context.project_id,
                        platform_id=context.platform_id,
                        visitor_id=context.visitor_id,
                        source_message_id=source,
                        message_type=2 if request.msg_type == 2 else 4,
                        reference=request.message,
                        file_id=request.media_file_id,
                    ),
                    classify=False,
                )
            except MediaInputError as error:
                failures.append(error.message)
                normalized.append(
                    {
                        "kind": "image" if request.msg_type == 2 else "voice",
                        "content": "本条媒体未识别，不能推测其中内容。",
                    }
                )
                continue
            normalized.append(
                {
                    "kind": "image" if request.msg_type == 2 else "voice",
                    "content": prepared.customer_message,
                }
            )
            understood += 1
            from app.models import MediaAnalysisResult
            from sqlalchemy import select

            with SessionLocal() as db:
                result = db.scalar(
                    select(MediaAnalysisResult).where(
                        MediaAnalysisResult.project_id == context.project_id,
                        MediaAnalysisResult.platform_id == context.platform_id,
                        MediaAnalysisResult.visitor_id == context.visitor_id,
                        MediaAnalysisResult.source_message_id == source,
                    )
                )
                if result:
                    for target, name in (
                        ("asr_text", "transcript"),
                        ("ocr_text", "ocr_text"),
                        ("vlm_text", "vision_summary"),
                    ):
                        text = getattr(result, name)
                        if text:
                            previous = context.media_input.get(target)
                            context.media_input[target] = (
                                str(previous) + "\n" if previous else ""
                            ) + text
                    previous_categories = context.media_input.get(
                        "sensitive_data_categories"
                    )
                    categories = (
                        set(previous_categories)
                        if isinstance(previous_categories, list)
                        else set()
                    )
                    categories.update(result.sensitive_data_categories)
                    context.media_input["sensitive_data_categories"] = sorted(
                        categories
                    )
        else:
            normalized.append({"kind": "text", "content": request.message})
            understood += 1
    await ensure_current()
    if failures and not understood:
        from app.services.chat_media_service import MediaInputError

        context.media_failure = failures[0]
        raise MediaInputError(context.media_failure)
    message = (
        normalized[0]["content"]
        if len(normalized) == 1
        else "同一客户按顺序发送了以下消息，请结合全部内容回答，不遗漏独立问题：\n"
        + json.dumps(normalized, ensure_ascii=False)
    )
    instructions = (
        "按客户消息的原顺序理解；后续修正替代旧条件，不同问题分别回答。媒体识别内容和客户原话只用于理解需求，不证明商品价格、库存或已执行操作。"
    )
    if failures:
        instructions += (
            "本轮有媒体识别失败：给出一次请重发或补充文字的提示，同时回答其他能够确认的问题；不能把历史图片当成本次未识别的图片。"
        )
    logging.getLogger(__name__).info(
        (
            'Customer turn phase turn=%s version=%s phase=recognition '
            'seconds=%.3f parts=%s failures=%s'
        ),
        context.turn_id,
        context.version,
        time.monotonic() - started,
        len(parts),
        len(failures),
    )
    return message, instructions


async def stage_reply(reply: str, sender: str) -> bool:
    (
        "True defers final publication to the external channel's delivery "
        'receipt.'
    )
    context = current_execution.get()
    if context is None:
        return False
    await ensure_current()
    from app.services.ai_usage_runtime import (
        current_permit,
        defer_current_permit,
    )

    permit = current_permit()
    saved = (
        {
            "reservation_id": str(permit.reservation_id),
            "lease_id": str(permit.lease_id),
        }
        if permit
        else None
    )
    with SessionLocal() as db:
        store = TurnStore(db)
        store.stage(
            context.turn_id,
            context.version,
            reply,
            sender,
            saved if context.external else None,
            owner=context.owner,
        )
        if not context.external:
            store.claim(context.turn_id, context.version)
        db.commit()
    context.staged = True
    if context.generation_started:
        logging.getLogger(__name__).info(
            (
                'Customer turn phase turn=%s version=%s '
                'phase=generation_expression seconds=%.3f'
            ),
            context.turn_id,
            context.version,
            time.monotonic() - context.generation_started,
        )
    if context.external:
        defer_current_permit()
    return context.external


async def confirm_im() -> None:
    context = current_execution.get()
    if context is None or context.external:
        return
    with SessionLocal() as db:
        store = TurnStore(db)
        turn = store.get(context.turn_id, lock=True)
        if turn.claim_id is None:
            raise TurnSuperseded()
        store.confirm(
            turn.id, context.version, turn.claim_id, "sent", turn.client_msg_no
        )
        from datetime import datetime, timezone

        turn.im_published_at = datetime.now(timezone.utc)
        db.commit()
