"""Run, recover and cancel one persisted company-owned configuration test."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from typing import Protocol, cast
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models.business_connection import BusinessConnection
from app.models.business_connection_test import BusinessConnectionTest
from app.schemas.business_connection_tests import (
    BusinessConnectionTestRequest,
    BusinessConnectionTestReport,
    TestStatus,
)
from app.schemas.business_connections import ConnectionMode
from app.schemas.connection_test_protocol import (
    ConnectionTestCommand,
    ConnectionTestReceipt,
    Operation,
)
from app.services.agent_activation_requirements import require_activation_actor
from app.services import business_connections as connections
from app.services.business_connection_proofs import (
    current_capabilities,
    parsed_receipt,
    utc,
    assess_report,
)
from app.services.plugin_runtime_client import plugin_runtime_client


class TestAdapter(Protocol):
    async def test_business_connection(
        self, command: ConnectionTestCommand
    ) -> ConnectionTestReceipt:
        ...


@dataclass(frozen=True)
class TestClaim:
    report: BusinessConnectionTestReport
    command: ConnectionTestCommand | None = None
    attempt_id: UUID | None = None


def view(
    row: BusinessConnectionTest, db: Session, connection: BusinessConnection
) -> BusinessConnectionTestReport:
    receipt = parsed_receipt(row)
    return BusinessConnectionTestReport(
        id=row.id,
        request_id=row.request_id,
        connection_id=row.connection_id,
        operation=cast(Operation, row.operation),
        mode=cast(ConnectionMode, row.mode),
        status=cast(TestStatus, row.status),
        outcome=receipt.outcome if receipt else None,
        summary=receipt.summary if receipt else None,
        configuration_revision=row.configuration_revision,
        error_code=row.error_code,
        created_at=utc(row.created_at),
        completed_at=utc(row.completed_at) if row.completed_at else None,
        expires_at=utc(row.expires_at),
        applicability=assess_report(db, row, connection),
    )


class BusinessConnectionTestService:
    def __init__(
        self, db: Session, *, adapter: TestAdapter = plugin_runtime_client
    ):
        self.db, self.adapter = db, adapter

    def _row(
        self, project: UUID, connection: UUID, request: UUID
    ) -> BusinessConnectionTest:
        row = self.db.scalar(
            select(BusinessConnectionTest)
            .where(
                BusinessConnectionTest.project_id == project,
                BusinessConnectionTest.connection_id == connection,
                BusinessConnectionTest.request_id == request,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError("Business connection test")
        return row

    def _cache(self, connection: BusinessConnection) -> None:
        # These fields are only a cache; reads validate persisted reports
        # again.
        connection.verified_configuration_revision = (
            connection.configuration_revision
        )
        connection.verified_capabilities = current_capabilities(
            self.db, connection
        )

    def _expire(
        self, row: BusinessConnectionTest, connection: BusinessConnection
    ) -> None:
        if row.status == "running" and utc(row.expires_at) <= datetime.now(
            UTC
        ):
            row.status, row.error_code, row.completed_at = (
                "failed",
                "timeout",
                datetime.now(UTC),
            )
            self.db.flush()
            self._cache(connection)
            self.db.commit()

    def get(
        self, project: UUID, actor: UUID, connection: UUID, request: UUID
    ) -> BusinessConnectionTestReport:
        require_activation_actor(self.db, project, actor)
        owned = connections._find(self.db, project, connection, lock=True)
        row = self._row(project, connection, request)
        self._expire(row, owned)
        result = view(row, self.db, owned)
        self.db.rollback()
        return result

    def list(
        self, project: UUID, actor: UUID, connection: UUID, limit: int = 50
    ) -> list[BusinessConnectionTestReport]:
        require_activation_actor(self.db, project, actor)
        owned = connections._find(self.db, project, connection, lock=True)
        rows = list(
            self.db.scalars(
                select(BusinessConnectionTest)
                .where(
                    BusinessConnectionTest.project_id == project,
                    BusinessConnectionTest.connection_id == connection,
                )
                .order_by(
                    BusinessConnectionTest.created_at.desc(),
                    BusinessConnectionTest.id.desc(),
                )
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        )
        for row in rows:
            self._expire(row, owned)
        result = [view(row, self.db, owned) for row in rows]
        self.db.rollback()
        return result

    def claim(
        self,
        project: UUID,
        actor: UUID,
        connection: UUID,
        payload: BusinessConnectionTestRequest,
    ) -> TestClaim:
        require_activation_actor(self.db, project, actor)
        owned = connections._find(self.db, project, connection, lock=True)
        digest = hmac.new(
            settings.SECRET_KEY.encode(),
            b"connection-test:"
            + str(project).encode()
            + str(actor).encode()
            + str(connection).encode()
            + payload.model_dump_json().encode(),
            hashlib.sha256,
        ).hexdigest()
        existing = self.db.scalar(
            select(BusinessConnectionTest)
            .where(
                BusinessConnectionTest.project_id == project,
                BusinessConnectionTest.connection_id == connection,
                BusinessConnectionTest.request_id == payload.request_id,
            )
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_digest, digest):
                raise ConflictError("原测试编号对应的内容或提交人不同，请查询原请求")
            self._expire(existing, owned)
            report = view(existing, self.db, owned)
            self.db.rollback()
            return TestClaim(report)
        if owned.revision != payload.expected_revision:
            raise ConflictError("连接已修改，请读取当前配置后测试")
        if payload.operation not in owned.capabilities:
            raise HTTPException(422, "此连接未声明所选业务能力")
        now = datetime.now(UTC)
        row = BusinessConnectionTest(
            id=uuid4(),
            project_id=project,
            actor_id=actor,
            connection_id=connection,
            request_id=payload.request_id,
            request_digest=digest,
            expected_revision=owned.revision,
            configuration_revision=owned.configuration_revision,
            authorization_revision=owned.authorization_revision,
            resource_revision="0" * 64,
            operation=payload.operation,
            mode=owned.mode,
            status="running",
            attempt_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(seconds=120),
        )
        self.db.add(row)
        self.db.flush()
        self._cache(owned)
        row.resource_revision = connections._view(
            self.db, owned
        ).resource_revision
        command = ConnectionTestCommand(
            project_id=project,
            actor_id=actor,
            connection_id=connection,
            request_id=row.request_id,
            expected_revision=row.expected_revision,
            configuration_revision=row.configuration_revision,
            authorization_revision=row.authorization_revision,
            resource_revision=row.resource_revision,
            operation=payload.operation,
            order_no=payload.order_no,
            customer_id=payload.customer_id,
        )
        claim = TestClaim(view(row, self.db, owned), command, row.attempt_id)
        self.db.commit()
        return claim

    def finish(
        self,
        project: UUID,
        connection: UUID,
        report_id: UUID,
        attempt_id: UUID | None,
        *,
        receipt: ConnectionTestReceipt | None = None,
        error_code: str | None = None,
    ) -> BusinessConnectionTestReport:
        owned = connections._find(self.db, project, connection, lock=True)
        row = self.db.scalar(
            select(BusinessConnectionTest)
            .where(
                BusinessConnectionTest.id == report_id,
                BusinessConnectionTest.project_id == project,
                BusinessConnectionTest.connection_id == connection,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError("Business connection test")
        self._expire(row, owned)
        if row.status != "running" or row.attempt_id != attempt_id:
            result = view(row, self.db, owned)
            self.db.rollback()
            return result
        now = datetime.now(UTC)
        if receipt is not None:
            try:
                if row.actor_id is None:
                    raise HTTPException(403, "原管理员已不可用")
                require_activation_actor(self.db, project, row.actor_id)
                fields = (
                    "project_id",
                    "actor_id",
                    "connection_id",
                    "expected_revision",
                    "configuration_revision",
                    "authorization_revision",
                    "resource_revision",
                    "request_id",
                    "operation",
                    "mode",
                )
                if (
                    any(
                        getattr(receipt, field) != getattr(row, field)
                        for field in fields
                    )
                    or owned.revision != row.expected_revision
                    or owned.configuration_revision
                    != row.configuration_revision
                    or owned.authorization_revision
                    != row.authorization_revision
                    or owned.mode != row.mode
                    or connections._view(self.db, owned).resource_revision
                    != row.resource_revision
                    or receipt.checked_at
                    < utc(row.created_at) - timedelta(seconds=5)
                    or receipt.checked_at > now + timedelta(seconds=5)
                ):
                    raise HTTPException(409, "原配置或测试回执已改变")
            except HTTPException:
                receipt, error_code = None, "authority_changed"
        row.completed_at = now
        if receipt is not None:
            row.receipt = cast(
                dict[str, JsonValue], receipt.model_dump(mode="json")
            )
            row.status = (
                "failed" if receipt.outcome == "failed" else "completed"
            )
            row.error_code = receipt.error_code
            owned.last_tested_at, owned.last_tested_by = now, row.actor_id
        else:
            row.status = "cancelled" if error_code == "cancelled" else "failed"
            row.error_code = error_code or "provider_unavailable"
        self.db.flush()
        self._cache(owned)
        result = view(row, self.db, owned)
        self.db.commit()
        return result

    def cancel(
        self, project: UUID, actor: UUID, connection: UUID, request: UUID
    ) -> BusinessConnectionTestReport:
        require_activation_actor(self.db, project, actor)
        owned = connections._find(self.db, project, connection, lock=True)
        row = self._row(project, connection, request)
        self._expire(row, owned)
        if row.status == "running":
            row.status, row.error_code, row.completed_at = (
                "cancelled",
                "cancelled",
                datetime.now(UTC),
            )
            self.db.flush()
            self._cache(owned)
            self.db.commit()
        result = view(row, self.db, owned)
        self.db.rollback()
        return result

    async def run(
        self,
        project: UUID,
        actor: UUID,
        connection: UUID,
        payload: BusinessConnectionTestRequest,
    ) -> BusinessConnectionTestReport:
        claim = self.claim(project, actor, connection, payload)
        if claim.command is None:
            return claim.report
        try:
            receipt = await self.adapter.test_business_connection(
                claim.command
            )
        except asyncio.CancelledError:
            self.db.rollback()
            self.finish(
                project,
                connection,
                claim.report.id,
                claim.attempt_id,
                error_code="cancelled",
            )
            raise
        except Exception:
            self.db.rollback()
            return self.finish(
                project,
                connection,
                claim.report.id,
                claim.attempt_id,
                error_code="provider_unavailable",
            )
        return self.finish(
            project,
            connection,
            claim.report.id,
            claim.attempt_id,
            receipt=receipt,
        )
