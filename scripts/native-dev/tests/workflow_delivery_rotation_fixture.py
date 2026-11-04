"""Owned API fixtures for frozen-delivery rotation verification."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import delete, inspect

from app.core.database import Base, SessionLocal
from app.core.security import create_access_token
from app.models import Platform, Project, SessionCommandDelivery, Staff
from app.models import Visitor, VisitorSession
from app.models.company_account import CompanyAccount
from app.models.workflow_event_subscription import WorkflowEventDelivery
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


@dataclass
class DeliveryState:
    status: str
    attempts: int
    ciphertext: str | None = field(repr=False)
    error: str | None = None
    execution_id: str | None = None


@dataclass
class CipherFixture:
    project_id: UUID = field(default_factory=uuid4)
    staff_id: UUID = field(default_factory=uuid4)
    platform_id: UUID = field(default_factory=uuid4)
    visitor_id: UUID = field(default_factory=uuid4)
    session_id: UUID = field(default_factory=uuid4)
    command_id: UUID = field(default_factory=uuid4)
    delivery_id: UUID = field(default_factory=uuid4)
    owner_key: SecretStr = field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(48)),
        repr=False,
    )

    @property
    def name(self) -> str:
        return "cipher-delivery-live-" + self.project_id.hex

    @property
    def username(self) -> str:
        return "cipher-" + self.project_id.hex[:24] + "@example.test"

    @property
    def channel(self) -> str:
        return build_visitor_channel_id(self.visitor_id)

    @property
    def staff_uid(self) -> str:
        return str(self.staff_id) + "-staff"

    def seed(self) -> None:
        with SessionLocal() as db:
            db.add(
                Project(
                    id=self.project_id,
                    name=self.name,
                    api_key=self.owner_key.get_secret_value(),
                )
            )
            db.flush()
            db.add(
                Staff(
                    id=self.staff_id,
                    project_id=self.project_id,
                    username=self.username,
                    role="admin",
                    account_enabled=True,
                    password_hash="disabled-cipher-fixture-login",
                    email_verified_at=datetime.now(timezone.utc),
                    is_active=True,
                    service_paused=False,
                    token_version=1,
                )
            )
            db.add(
                Platform(
                    id=self.platform_id,
                    project_id=self.project_id,
                    type="website",
                    name="临时人工网页渠道",
                    is_active=True,
                    ai_mode="off",
                    api_key=secrets.token_urlsafe(48),
                )
            )
            db.add(
                CompanyAccount(
                    project_id=self.project_id,
                    status="trial",
                    trial_granted=True,
                    seat_limit=2,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                )
            )
            db.flush()
            db.add(
                Visitor(
                    id=self.visitor_id,
                    project_id=self.project_id,
                    platform_id=self.platform_id,
                    platform_open_id=uuid4().hex,
                    ai_disabled=True,
                    service_mode="manual",
                )
            )
            db.flush()
            db.add(
                VisitorSession(
                    id=self.session_id,
                    project_id=self.project_id,
                    visitor_id=self.visitor_id,
                    platform_id=self.platform_id,
                    staff_id=self.staff_id,
                    status="open",
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(minutes=1),
                )
            )
            db.commit()

    def token(self) -> str:
        return create_access_token(
            subject=self.username,
            project_id=self.project_id,
            role="admin",
            expires_delta=timedelta(minutes=5),
            token_version=1,
        )

    def stage(
        self,
        workflow: str,
        subscription: str,
        release: str,
        digest: str,
        revision: int,
        maximum: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        version = now.replace(tzinfo=None)
        with SessionLocal() as db:
            session = db.get(VisitorSession, self.session_id)
            assert session is not None
            session.status = "closed"
            session.closed_at = version
            session.updated_at = version
            session.last_message_seq = maximum
            db.add(
                SessionCommandDelivery(
                    id=self.command_id,
                    project_id=self.project_id,
                    visitor_id=self.visitor_id,
                    session_id=self.session_id,
                    actor_staff_id=self.staff_id,
                    kind="close",
                    session_version=version,
                    actor_role="admin",
                    actor_token_version=1,
                    actor_token_expires_at=now + timedelta(hours=1),
                    client_msg_no="cipher-" + uuid4().hex,
                    channel_id=self.channel,
                    channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                    from_uid=self.staff_uid,
                    payload={},
                    status="skipped",
                    effects_status="skipped",
                    created_at=now,
                )
            )
            db.flush()
            db.add(
                WorkflowEventDelivery(
                    id=self.delivery_id,
                    project_id=self.project_id,
                    workflow_id=workflow,
                    subscription_id=UUID(subscription),
                    subscription_revision=revision,
                    source_command_id=self.command_id,
                    session_id=self.session_id,
                    release_id=release,
                    release_digest=digest,
                    status="sending",
                    attempts=1,
                    lease_until=now + timedelta(minutes=2),
                    next_attempt_at=now + timedelta(minutes=5),
                )
            )
            db.commit()

    def state(self) -> DeliveryState:
        with SessionLocal() as db:
            row = db.get(WorkflowEventDelivery, self.delivery_id)
            assert row is not None and row.project_id == self.project_id
            return DeliveryState(
                row.status,
                row.attempts,
                row.encrypted_payload,
                row.last_error,
                row.execution_id,
            )

    def claim(self, attempt: int) -> None:
        with SessionLocal() as db:
            row = db.get(WorkflowEventDelivery, self.delivery_id)
            assert row is not None and row.project_id == self.project_id
            assert row.status == "pending" and row.attempts == attempt - 1
            row.status, row.attempts = "sending", attempt
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=2)
            db.commit()

    def park(self) -> None:
        with SessionLocal() as db:
            row = db.get(WorkflowEventDelivery, self.delivery_id)
            assert row is not None and row.project_id == self.project_id
            assert row.status == "pending"
            row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                minutes=5
            )
            db.commit()

    def cleanup(self) -> None:
        with SessionLocal() as db:
            owner = db.get(Project, self.project_id)
            if owner is not None and (
                owner.name != self.name
                or owner.api_key != self.owner_key.get_secret_value()
            ):
                raise RuntimeError("Cipher fixture ownership mismatch")
            names = set(inspect(db.get_bind()).get_table_names())
            for table in reversed(Base.metadata.sorted_tables):
                if (
                    table.name in names
                    and table.name.startswith("api_")
                    and "project_id" in table.c
                ):
                    db.execute(
                        delete(table).where(
                            table.c.project_id == self.project_id
                        )
                    )
            db.execute(delete(Project).where(Project.id == self.project_id))
            db.commit()
            assert db.get(Project, self.project_id) is None
