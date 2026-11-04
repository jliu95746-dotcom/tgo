"""Terminal runs release inputs while preserving invocation identity."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.services.execution_lifecycle import (
    cancel_running_execution,
    fail_pending_dispatch,
    finish_running_execution,
)
from app.services.execution_snapshot import input_receipt_hmac


def test_input_receipt_hmac_is_canonical_and_keyed():
    first = input_receipt_hmac(
        {"question": "客户原话", "count": 2}, "synthetic-secret-a", "run-1"
    )
    reordered = input_receipt_hmac(
        {"count": 2, "question": "客户原话"}, "synthetic-secret-a", "run-1"
    )
    assert first == reordered
    assert len(first) == 64
    assert first != input_receipt_hmac(
        {"question": "另一问题", "count": 2}, "synthetic-secret-a", "run-1"
    )
    assert first != input_receipt_hmac(
        {"question": "客户原话", "count": 2}, "synthetic-secret-b", "run-1"
    )
    assert first != input_receipt_hmac(
        {"question": "客户原话", "count": 2}, "synthetic-secret-a", "run-2"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["finish", "dispatch_failure", "cancel"])
async def test_terminal_transition_drops_original_input(transition):
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalar_one_or_none=Mock(return_value="run-1"),
        # This test inspects the terminal update; the real cleanup transaction
        # and its source/approval races are covered by database-backed tests.
        one_or_none=Mock(return_value=None),
    )
    db.scalar.return_value = "run-1"
    if transition == "finish":
        assert await finish_running_execution(
            db,
            "run-1",
            "project-1",
            "completed",
            {},
            None,
            1,
        )
    elif transition == "dispatch_failure":
        assert await fail_pending_dispatch(db, "run-1", "project-1")
    else:
        await cancel_running_execution(db, "run-1", "project-1")
    statement = (
        db.scalar.await_args.args[0]
        if transition == "cancel"
        else db.execute.await_args_list[0].args[0]
    )
    values = statement.compile().params
    assert "input" in values
    assert values["input"] is None
