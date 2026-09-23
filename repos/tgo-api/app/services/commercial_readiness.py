"""Inspect configuration locally; never send mail, create payments or reveal keys."""

from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.commercial_readiness import CommercialReadiness, ReadinessItem
from app.services.company_email import require_mail_configuration
from app.services.wechat_pay_client import WeChatPayClient


def readiness(db: Session | None = None) -> CommercialReadiness:
    email = payment = False
    try:
        require_mail_configuration()
        email = True
    except (HTTPException, ValueError):
        pass
    try:
        WeChatPayClient()  # Parses local keys only; no network request.
        payment = True
    except (TGOAPIException, ValueError):
        pass
    try:
        refund = urlsplit(settings.WECHAT_PAY_REFUND_NOTIFY_URL)
        refund_ready = bool(
            refund.scheme == "https"
            and refund.hostname
            and not any((refund.query, refund.fragment, refund.username))
        )
    except ValueError:
        refund_ready = False
    token = settings.SAAS_INTERNAL_TOKEN
    result = CommercialReadiness(
        billing_enabled=settings.SAAS_ENABLED and settings.SAAS_BILLING_ENABLED,
        purchases_enabled=settings.SAAS_ENABLED
        and settings.SAAS_BILLING_ENABLED
        and settings.SAAS_NEW_PURCHASES_ENABLED,
        registration_enabled=settings.SAAS_ENABLED
        and settings.SAAS_BILLING_ENABLED
        and settings.SAAS_REGISTRATION_ENABLED
        and settings.PUBLIC_REGISTRATION_ENABLED,
        checks=[
            ReadinessItem(code="email", configured=email),
            ReadinessItem(code="wechat", configured=payment),
            ReadinessItem(code="refund_callback", configured=refund_ready),
            ReadinessItem(
                code="internal_auth",
                configured=bool(token and len(token.get_secret_value()) >= 32),
            ),
        ],
    )
    if db is not None:
        from app.services.platform_models import runtime_model

        try:
            model_ready = runtime_model(db) is not None
        except (TGOAPIException, ValueError):
            model_ready = False
        result.checks.append(
            ReadinessItem(code="platform_model", configured=model_ready)
        )
    return result
