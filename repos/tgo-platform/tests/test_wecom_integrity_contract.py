from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.api import wecom_utils


class FailingSession:
    def __init__(self, error: IntegrityError) -> None:
        self.error = error
        self.rollbacks = 0

    def add(self, _: object) -> None:
        return None

    async def commit(self) -> None:
        raise self.error

    async def rollback(self) -> None:
        self.rollbacks += 1


def integrity_error(sqlstate: str, constraint_name: str) -> IntegrityError:
    original = RuntimeError(f'constraint "{constraint_name}"')
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(constraint_name=constraint_name)
    return IntegrityError("INSERT", {}, original)


async def store_with(error: IntegrityError) -> wecom_utils.InboxStoreResult:
    return await wecom_utils.try_store_wecom_inbox(
        FailingSession(error),
        platform_id=uuid4(),
        message_id="message-id",
        source_type="wecom_kf",
        from_user="external-user",
        msg_type="text",
        content="hello",
    )


@pytest.mark.asyncio
async def test_only_expected_unique_constraint_is_a_duplicate() -> None:
    duplicate_result = await store_with(
        integrity_error("23505", "uq_wecom_inbox_platform_message")
    )
    foreign_key_result = await store_with(
        integrity_error("23503", "pt_wecom_inbox_platform_id_fkey")
    )

    assert duplicate_result == wecom_utils.InboxStoreResult.DUPLICATE
    assert foreign_key_result == wecom_utils.InboxStoreResult.ERROR
