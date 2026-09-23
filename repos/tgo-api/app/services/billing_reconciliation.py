"""Daily signed statement reconciliation, with query-based payment compensation."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.billing import BillingJob, BillingOrder, BillingRefund
from app.models.billing_reconciliation import BillingReconciliation
from app.services.billing_orders import record_verified_payment
from app.services.billing_refunds import record_refund_result
from app.services.company_email import utc
from app.services.wechat_bill_parser import parse_trade_bill
from app.services.wechat_pay_client import WeChatPayClient, WeChatPayError

CHINA = ZoneInfo("Asia/Shanghai")


def schedule_statements() -> None:
    local_now = datetime.now(CHINA)
    last_day = local_now.date() - timedelta(days=1 if local_now.hour >= 10 else 2)
    with SessionLocal() as db:
        earliest = db.scalar(select(func.min(BillingOrder.created_at)))
        if earliest is None:
            return
        first_day = max(
            utc(earliest).astimezone(CHINA).date(), last_day - timedelta(days=89)
        )
        for day_offset in range((last_day - first_day).days + 1):
            day = first_day + timedelta(days=day_offset)
            key = f"tradebill:{day.isoformat()}"
            if (
                db.scalar(select(BillingJob.id).where(BillingJob.business_key == key))
                is None
            ):
                try:
                    with db.begin_nested():
                        db.add(
                            BillingJob(
                                business_key=key,
                                kind="tradebill",
                                available_at=datetime.now(timezone.utc),
                            )
                        )
                        db.flush()
                except IntegrityError:
                    pass
        db.commit()


def reconcile_statement(day: date) -> str:
    with SessionLocal() as db:
        previous = db.get(BillingReconciliation, day)
        if previous is not None:
            return "review" if previous.issue_count else "done"
    client = WeChatPayClient()
    try:
        content, digest = client.download_trade_bill(day)
        entries = parse_trade_bill(content)
    except WeChatPayError as exc:
        if exc.provider_code != "NO_STATEMENT_EXIST":
            raise
        entries, digest = [], "no-statement"
    issues: list[dict[str, JsonValue]] = []
    issue_count = 0
    seen: set[str] = set()

    def issue(code: str, number: str, project: str | None = None) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 200:
            issues.append({"code": code, "order_number": number, "project_id": project})

    for entry in entries:
        seen.add(entry.number)
        with SessionLocal() as db:
            order = db.scalar(
                select(BillingOrder).where(BillingOrder.number == entry.number)
            )
            if order is None:
                issue("PROVIDER_ORDER_UNKNOWN", entry.number)
                continue
            project = str(order.project_id)
            if (
                entry.amount != order.amount
                or entry.currency != "CNY"
                or entry.merchant_id != settings.WECHAT_PAY_MCH_ID
                or entry.app_id != settings.WECHAT_PAY_APP_ID
                or (
                    order.transaction_id
                    and entry.transaction_id != order.transaction_id
                )
            ):
                issue("STATEMENT_ORDER_MISMATCH", entry.number, project)
                continue
            pending = order.payment_status != "paid"
        if pending:
            transaction = client.query(entry.number)
            if (
                transaction.trade_state != "SUCCESS"
                or transaction.transaction_id is None
                or transaction.success_time is None
                or transaction.amount is None
                or transaction.transaction_id != entry.transaction_id
                or transaction.amount.total != entry.amount
                or transaction.amount.currency != entry.currency
            ):
                issue("STATEMENT_QUERY_MISMATCH", entry.number, project)
                continue
            with SessionLocal() as db:
                record_verified_payment(
                    db,
                    number=entry.number,
                    transaction_id=transaction.transaction_id,
                    amount=transaction.amount.total,
                    currency=transaction.amount.currency,
                    paid_at=transaction.success_time,
                    event_key=f"bill:{day}:{transaction.transaction_id}",
                )
                db.commit()
        if entry.refund_number:
            with SessionLocal() as db:
                refund = db.scalar(
                    select(BillingRefund).where(
                        BillingRefund.number == entry.refund_number
                    )
                )
                known = refund is not None
                matches_order = refund is not None and (
                    refund.order_id == order.id
                    and refund.project_id == order.project_id
                )
            if not known:
                issue("PROVIDER_REFUND_UNKNOWN", entry.number, project)
            elif not matches_order:
                issue("STATEMENT_REFUND_MISMATCH", entry.number, project)
            else:
                result = client.query_refund(entry.refund_number)
                if result.out_refund_no != entry.refund_number:
                    raise ValueError("Refund statement query mismatch")
                with SessionLocal() as db:
                    record_refund_result(db, result)
                    db.commit()
    start = datetime.combine(day, time.min, CHINA).astimezone(timezone.utc)
    with SessionLocal() as db:
        for order in db.scalars(
            select(BillingOrder).where(
                BillingOrder.payment_status == "paid",
                BillingOrder.amount > 0,
                BillingOrder.paid_at >= start,
                BillingOrder.paid_at < start + timedelta(days=1),
            )
        ):
            if order.number not in seen:
                issue(
                    "LOCAL_PAYMENT_MISSING_FROM_BILL",
                    order.number,
                    str(order.project_id),
                )
        db.add(
            BillingReconciliation(
                bill_date=day,
                status="review" if issue_count else "matched",
                source_hash=digest,
                entry_count=len(entries),
                issue_count=issue_count,
                issues=issues,
            )
        )
        db.commit()
    return "review" if issue_count else "done"
