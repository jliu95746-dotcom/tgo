"""Issue operator-controlled trial codes and redeem once per verified company."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Staff
from app.models.company_account import AICreditBatch, CompanyAccount
from app.models.platform_operator import PlatformOperator
from app.models.trial_activation_code import TrialActivationCode
from app.services.company_email import utc
from app.services.company_membership import lock_company
from app.services.trial_policy import read_policy


def issue_code(
    db: Session, operator: PlatformOperator
) -> tuple[str, TrialActivationCode]:
    code = f"YJ-{token_urlsafe(24)}"
    record = TrialActivationCode(
        code_hash=sha256(code.encode("utf-8")).hexdigest(),
        operator_id=operator.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(record)
    db.flush()
    return code, record


def redeem_code(db: Session, actor: Staff, code: str) -> CompanyAccount:
    account = lock_company(db, actor.project_id, actor)
    if actor.email_verified_at is None:
        raise HTTPException(403, "请先完成企业邮箱验证")
    if account is None or account.status != "pending" or account.trial_granted:
        raise HTTPException(409, "当前企业不能重复开通试用")
    record = db.scalar(
        select(TrialActivationCode)
        .where(
            TrialActivationCode.code_hash == sha256(code.encode("utf-8")).hexdigest()
        )
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if (
        record is None
        or record.redeemed_at is not None
        or utc(record.expires_at) <= now
    ):
        raise HTTPException(400, "激活码无效、过期或已使用")
    trial = read_policy(db)
    account.status = "trial"
    account.trial_granted = True
    account.started_at = now
    account.expires_at = now + timedelta(days=trial.days)
    account.seat_limit = trial.seats
    account.version += 1
    record.redeemed_at = now
    record.redeemed_project_id = account.project_id
    db.add(
        AICreditBatch(
            project_id=account.project_id,
            source_key=f"trial:{account.project_id}",
            kind="trial",
            amount=trial.ai_replies,
            remaining=trial.ai_replies,
            expires_at=account.expires_at,
        )
    )
    return account
