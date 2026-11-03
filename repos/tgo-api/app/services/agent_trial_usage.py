"""Trial billing reconciles owned AI reports instead of inventing IM messages."""

import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import TGOAPIException
from app.models.ai_usage import AIUsageReservation
from app.models.company_account import CompanyAccount
from app.schemas.agent_trials import AgentTrialReceipt, AgentTrialReport, AgentTrialRequest
from app.services import ai_usage
from app.services.ai_client import ai_client
from app.services.ai_usage_runtime import ReplyPermit
from app.services.company_email import utc
from app.services.company_membership import lock_company


def _round_key(staff_id: UUID, request_key: UUID) -> str:
    return f"trial:{staff_id}:{request_key}"


def _receipt(row: AIUsageReservation) -> AgentTrialReceipt:
    try:
        return AgentTrialReceipt.model_validate(row.receipt)
    except ValidationError:
        raise TGOAPIException("试聊用量记录无效，请等待核验", code="AI_TRIAL_RECEIPT_INVALID", status_code=409) from None


def reserve_trial(
    project_id: UUID, staff_id: UUID, agent_id: UUID, request: AgentTrialRequest,
) -> ReplyPermit | None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return None
    digest = hmac.new(settings.SECRET_KEY.encode(), (
        str(agent_id) + ":" + request.model_dump_json()
    ).encode(), hashlib.sha256).hexdigest()
    receipt = AgentTrialReceipt(
        staff_id=staff_id, agent_id=agent_id, request_key=request.request_key, request_digest=digest,
    )
    with SessionLocal() as db:
        lock_company(db, project_id)
        row = db.scalar(select(AIUsageReservation).where(
            AIUsageReservation.project_id == project_id,
            AIUsageReservation.round_key == _round_key(staff_id, request.request_key),
        ).execution_options(populate_existing=True))
        if row is not None:
            previous = _receipt(row)
            if previous.request_digest != digest or previous.agent_id != agent_id or previous.staff_id != staff_id:
                raise TGOAPIException("原试聊请求已存在，请使用新的请求重新测试", code="AI_REQUEST_CONFLICT", status_code=409)
            if row.status != "released":
                if row.status != "reserved" or utc(row.expires_at) <= datetime.now(timezone.utc):
                    raise TGOAPIException("原试聊结果正在核验，请查询原报告", code="AI_ROUND_ALREADY_CLAIMED", status_code=409)
                ai_usage.require_service(db.get(CompanyAccount, project_id), datetime.now(timezone.utc))
                result = ReplyPermit(project_id, row.id, row.lease_id, False)
                db.commit()
                return result
        row = ai_usage.reserve(db, project_id, _round_key(staff_id, request.request_key))
        if row is None:
            db.commit()
            return None
        row.receipt = receipt.model_dump(mode="json")
        result = ReplyPermit(project_id, row.id, row.lease_id, False)
        db.commit()
        return result


def finish_trial(project_id: UUID, staff_id: UUID, report: AgentTrialReport) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(AIUsageReservation).where(
            AIUsageReservation.project_id == project_id,
            AIUsageReservation.round_key == _round_key(staff_id, report.request_key),
        ))
        if row is None:
            return  # Existing legacy/unmetered reports are not charged retroactively.
        row = ai_usage.owned(db, project_id, row.id, row.lease_id)
        receipt = _receipt(row)
        if receipt.agent_id != report.agent_id or receipt.staff_id != staff_id or receipt.request_key != report.request_key:
            raise TGOAPIException("试聊报告与用量记录不一致", code="AI_TRIAL_RECEIPT_INVALID", status_code=409)
        if row.status in {"released", "settled"} or report.status == "running":
            db.commit()
            return
        row.receipt = receipt.model_copy(update={"report_id": report.id}).model_dump(mode="json")
        if report.status in {"completed", "blocked"} and report.answer.strip():
            # The AI authorized this lease before generation. Its durable report
            # is the trial receipt, including when network recovery happens later.
            if row.status == "reserved":
                row.status = "review"
                db.flush()
            ai_usage.settle(db, project_id, row.id, row.lease_id)
        else:
            ai_usage.release(db, project_id, row.id, row.lease_id, delivery_rejected=True)
        db.commit()


def defer_trial(permit: ReplyPermit) -> None:
    with SessionLocal() as db:
        row = ai_usage.owned(db, permit.project_id, permit.reservation_id, permit.lease_id)
        _receipt(row)
        if row.status not in {"settled", "released"}:
            row.status = "review"
            row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.commit()


def release_trial(permit: ReplyPermit) -> None:
    with SessionLocal() as db:
        row = ai_usage.owned(db, permit.project_id, permit.reservation_id, permit.lease_id)
        _receipt(row)
        ai_usage.release(db, permit.project_id, row.id, row.lease_id, delivery_rejected=True)
        db.commit()


def _expired_trials() -> list[tuple[ReplyPermit, AgentTrialReceipt]]:
    with SessionLocal() as db:
        rows = db.scalars(select(AIUsageReservation).where(
            AIUsageReservation.status.in_(["reserved", "publishing", "review"]),
            AIUsageReservation.expires_at <= datetime.now(timezone.utc),
            AIUsageReservation.receipt["kind"].as_string() == "agent_trial",
        ).order_by(AIUsageReservation.expires_at).limit(100))
        result: list[tuple[ReplyPermit, AgentTrialReceipt]] = []
        for row in rows:
            try:
                result.append((ReplyPermit(row.project_id, row.id, row.lease_id, False), _receipt(row)))
            except TGOAPIException:
                # Corrupt references cannot prove non-delivery. Preserve credit.
                row.status = "review"
                row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.commit()
        return result


async def recover_trials() -> None:
    for permit, receipt in await asyncio.to_thread(_expired_trials):
        try:
            report = await ai_client.agent_trial_by_request(
                permit.project_id, receipt.staff_id, receipt.agent_id, receipt.request_key,
            )
            if report.status == "running":
                await asyncio.to_thread(defer_trial, permit)
            else:
                await asyncio.to_thread(finish_trial, permit.project_id, receipt.staff_id, report)
        except HTTPException as error:
            # AI claims the report before authorization or any model call. A
            # trusted 404 after the lease expires proves there is no generation.
            if error.status_code == 404:
                await asyncio.to_thread(release_trial, permit)
            else:
                await asyncio.to_thread(defer_trial, permit)
        except Exception:
            await asyncio.to_thread(defer_trial, permit)
