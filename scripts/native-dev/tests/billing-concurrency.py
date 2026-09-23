"""Exercise real PostgreSQL contention using only a disposable synthetic schema."""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))

from fastapi import HTTPException
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import Base, sync_engine
from app.core.exceptions import TGOAPIException
from app.models import Project, Staff
from app.models.company_account import CompanyAccount, AICreditBatch
from app.models.billing import BillingPlan, BillingJob, PaymentEvent, SubscriptionPeriod
from app.models.ai_usage import AIUsageReservation
from app.services.company_membership import lock_company, ensure_seat, protect_last_admin, seat_usage
from app.services.ai_usage import reserve
from app.services.billing_orders import create_order, record_verified_payment
from app.services.billing_quotes import create_quote
from app.services.billing_fulfillment import fulfill_order
from app.schemas.billing import PlanDefinition, QuoteRequest


def contend(operation):
    gate = Barrier(2)
    def run(index):
        gate.wait(timeout=10)
        return operation(index)
    with ThreadPoolExecutor(max_workers=2) as workers:
        return list(workers.map(run, range(2)))


def main():
    schema = "billing_eval_" + uuid4().hex
    assert re.fullmatch(r"billing_eval_[a-f0-9]{32}", schema)
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(settings.database_url_sync, connect_args={
        "options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=20000"})
    try:
        Base.metadata.create_all(engine)
        now = datetime.now(timezone.utc)
        with Session(engine) as db:
            project = Project(name="synthetic contention company", api_key=uuid4().hex)
            db.add(project); db.flush()
            company = CompanyAccount(project_id=project.id, status="trial", seat_limit=2,
                                     expires_at=now + timedelta(days=7))
            staff = [Staff(project_id=project.id, username=f"synthetic-{index}", password_hash="unused",
                           role="admin" if index == 0 else "user", account_enabled=index == 0) for index in range(3)]
            db.add_all([company, *staff]); db.commit()
            project_id, staff_ids = project.id, [person.id for person in staff]

        def enable(index):
            with Session(engine) as db:
                try:
                    company = lock_company(db, project_id)
                    ensure_seat(db, company)
                    db.get(Staff, staff_ids[index + 1]).account_enabled = True
                    db.commit(); return "enabled"
                except HTTPException as exc:
                    assert exc.status_code == 409
                    db.rollback(); return "rejected"
        assert sorted(contend(enable)) == ["enabled", "rejected"]
        with Session(engine) as db:
            assert seat_usage(db, project_id)[0] == 2
            enabled = list(db.scalars(select(Staff).where(Staff.account_enabled.is_(True))))
            for person in enabled: person.role = "admin"
            admin_ids = [person.id for person in enabled]
            db.commit()
        print("PASS: simultaneous activations cannot exceed the last available seat")

        def disable(index):
            with Session(engine) as db:
                try:
                    lock_company(db, project_id)
                    target = db.get(Staff, admin_ids[index])
                    protect_last_admin(db, target)
                    target.account_enabled = False
                    db.commit(); return "disabled"
                except HTTPException as exc:
                    assert exc.status_code == 409
                    db.rollback(); return "rejected"
        assert sorted(contend(disable)) == ["disabled", "rejected"]
        print("PASS: concurrent administrator removals preserve one enabled administrator")

        with Session(engine) as db:
            db.add(AICreditBatch(project_id=project_id, source_key="synthetic-last-credit", kind="trial",
                amount=1, remaining=1, expires_at=now + timedelta(days=7)))
            db.commit()
        def take_credit(index):
            with Session(engine) as db:
                try:
                    reserve(db, project_id, f"synthetic-round-{index}")
                    db.commit(); return "reserved"
                except TGOAPIException as exc:
                    assert exc.code == "AI_QUOTA_EXHAUSTED"
                    db.rollback(); return "rejected"
        assert sorted(contend(take_credit)) == ["rejected", "reserved"]
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(AIUsageReservation)) == 1
        print("PASS: concurrent replies reserve at most one remaining credit")

        with Session(engine) as db:
            definition = PlanDefinition(name="合成套餐", rank=1, monthly_price=3000, annual_price=30000,
                seats=3, monthly_ai=100, knowledge_bytes=100000, channel_limit=2,
                seat_monthly_price=500, seat_annual_price=5000, ai_pack_price=1000, ai_pack_replies=100)
            plan = BillingPlan(code="synthetic", version=1, status="published", definition=definition.model_dump(mode="json"))
            db.add(plan); db.flush()
            actor = db.scalar(select(Staff).where(Staff.account_enabled.is_(True)))
            quote = create_quote(db, actor, QuoteRequest(kind="subscribe", plan_id=plan.id, months=1))
            db.flush()
            order = create_order(db, actor, quote.id)
            db.commit()
            order_id, number = order.id, order.number
        def payment(_):
            with Session(engine) as db:
                record_verified_payment(db, number=number, transaction_id="synthetic-paid-transaction",
                    amount=3000, currency="CNY", paid_at=now, event_key="synthetic-repeated-event")
                db.commit()
        contend(payment)
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(PaymentEvent)) == 1
            assert db.scalar(select(func.count()).select_from(BillingJob)) == 1
        print("PASS: simultaneous duplicate payment notifications create one event and one fulfillment job")
        def fulfill(_):
            with Session(engine) as db:
                fulfill_order(db, order_id)
                db.commit()
        contend(fulfill)
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(SubscriptionPeriod)) == 1
            assert db.scalar(select(func.count()).select_from(AICreditBatch).where(AICreditBatch.order_id == order_id)) == 1
        print("PASS: parallel fulfillment creates one subscription period and one monthly credit batch")
    finally:
        engine.dispose()
        # Exact generated schema only; never run cleanup against application schemas.
        with sync_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        print("PASS: synthetic schema removed; main application tables unchanged")


if __name__ == "__main__":
    main()
