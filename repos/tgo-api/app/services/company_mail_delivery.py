"""Retryable mail delivery; SMTP runs outside the claiming transaction."""

import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.company_account import EmailAction, EmailOutbox
from app.services.company_email import MailPayload, utc
from app.utils.crypto import decrypt_str


def claim_mail(db: Session) -> tuple[UUID, UUID, str] | None:
    # Drain stale links without a worker sleep after every discarded message.
    # Bound each pass to keep shutdown responsive under backlog.
    for _ in range(100):
        claimed, discarded = _claim_next(db)
        if not discarded:
            return claimed
    return None


def _claim_next(db: Session) -> tuple[tuple[UUID, UUID, str] | None, bool]:
    now = datetime.now(timezone.utc)
    mail = db.scalar(
        select(EmailOutbox)
        .where(
            EmailOutbox.status == "pending",
            EmailOutbox.available_at <= now,
            or_(
                EmailOutbox.locked_until.is_(None),
                EmailOutbox.locked_until <= now,
            ),
        )
        .order_by(EmailOutbox.available_at, EmailOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if mail is None:
        return None, False
    action = db.get(EmailAction, mail.action_id)
    if (
        action is None
        or action.used_at is not None
        or utc(action.expires_at) <= now
    ):
        mail.status = "expired"
        mail.encrypted_payload = ""
        mail.locked_until = None
        mail.lease_id = None
        db.commit()
        return None, True
    lease = uuid4()
    mail.lease_id = lease
    mail.locked_until = now + timedelta(minutes=5)
    mail.attempts += 1
    result = mail.id, lease, mail.encrypted_payload
    db.commit()
    return result, False


def send_mail(payload: MailPayload) -> None:
    message = EmailMessage()
    message["From"] = settings.SAAS_SMTP_FROM
    message["To"] = str(payload.recipient)
    message["Subject"] = payload.subject
    message.set_content(payload.body)
    context = ssl.create_default_context()
    if settings.SAAS_SMTP_STARTTLS:
        client = smtplib.SMTP(
            settings.SAAS_SMTP_HOST, settings.SAAS_SMTP_PORT, timeout=20
        )
    else:
        client = smtplib.SMTP_SSL(
            settings.SAAS_SMTP_HOST,
            settings.SAAS_SMTP_PORT,
            timeout=20,
            context=context,
        )
    with client:
        if settings.SAAS_SMTP_STARTTLS:
            client.starttls(context=context)
        if settings.SAAS_SMTP_USER:
            secret = settings.SAAS_SMTP_PASSWORD
            if secret is None:
                raise ValueError("SMTP password is not configured")
            client.login(settings.SAAS_SMTP_USER, secret.get_secret_value())
        client.send_message(message)


def deliver_one() -> bool:
    with SessionLocal() as db:
        claimed = claim_mail(db)
    if claimed is None:
        return False
    identifier, lease, encrypted = claimed
    error: str | None = None
    try:
        plain = decrypt_str(encrypted)
        if plain is None:
            raise ValueError("Encrypted mail is invalid")
        send_mail(MailPayload.model_validate_json(plain))
    except Exception as exc:
        # SMTP errors can contain email addresses, tokens and credentials.
        error = type(exc).__name__
    with SessionLocal() as db:
        mail = db.scalar(
            select(EmailOutbox)
            .where(
                EmailOutbox.id == identifier,
                EmailOutbox.lease_id == lease,
                EmailOutbox.status == "pending",
            )
            .with_for_update()
        )
        if mail is None:
            return True
        now = datetime.now(timezone.utc)
        mail.last_error = error
        mail.locked_until = None
        mail.lease_id = None
        if error is None:
            mail.status = "sent"
            mail.sent_at = now
            mail.encrypted_payload = ""
        else:
            mail.available_at = now + timedelta(
                seconds=min(3600, 30 * 2 ** min(mail.attempts, 7))
            )
        db.commit()
    return True
