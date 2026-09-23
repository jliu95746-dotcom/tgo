"""Read immutable policy versions; atomically audit updates using optimistic versions."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models.billing import BillingAudit
from app.models.platform_operator import PlatformOperator
from app.models.trial_policy import TrialPolicy
from app.schemas.trial_policy import TrialPolicyChange, TrialPolicyResponse


def read_policy(db: Session) -> TrialPolicyResponse:
    latest = db.scalar(
        select(TrialPolicy).order_by(TrialPolicy.version.desc()).limit(1)
    )
    return TrialPolicyResponse(
        version=latest.version if latest else 0,
        ai_replies=latest.ai_replies if latest else settings.SAAS_TRIAL_AI_REPLIES,
        days=settings.SAAS_TRIAL_DAYS,
        seats=settings.SAAS_TRIAL_SEATS,
    )


def change_policy(
    db: Session, operator: PlatformOperator, payload: TrialPolicyChange
) -> TrialPolicyResponse:
    current = read_policy(db)
    if payload.expected_version != current.version:
        raise TGOAPIException(
            "试用配置已被修改，请刷新后重试", code="TRIAL_POLICY_CONFLICT", status_code=409
        )
    try:
        with db.begin_nested():
            db.add(
                TrialPolicy(
                    version=current.version + 1,
                    ai_replies=payload.ai_replies,
                    operator_id=operator.id,
                    reason=payload.reason,
                )
            )
            db.add(
                BillingAudit(
                    operator_id=operator.id,
                    action="trial_policy_change",
                    reason=payload.reason,
                    detail={
                        "version": current.version + 1,
                        "previous_ai_replies": current.ai_replies,
                        "ai_replies": payload.ai_replies,
                    },
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise TGOAPIException(
            "试用配置已被修改，请刷新后重试", code="TRIAL_POLICY_CONFLICT", status_code=409
        ) from exc
    return read_policy(db)
