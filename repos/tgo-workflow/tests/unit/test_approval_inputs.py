"""Approval proofs use frozen business input, never a storage pointer."""

import pytest

from app.models.execution import WorkflowExecution
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services.execution_snapshot import definition_digest
from tests.unit import test_webhook_dispatch as dispatch_fixtures

fixture = dispatch_fixtures.fixture
queued = dispatch_fixtures.queued


@pytest.mark.asyncio
async def test_pending_resume_digest_reads_the_frozen_payload(
    fixture, monkeypatch
):
    from app.services.approval_inputs import approval_input_digest

    identifier, source = await queued(fixture, monkeypatch)
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        run.status = "pending_resume"
        await db.commit()
        expected = definition_digest(
            {"data": {"question": "synthetic-private-question"}}
        )
        assert await approval_input_digest(db, run) == expected
        assert expected != definition_digest(run.input)
        row = await db.get(WorkflowWebhookDelivery, str(source))
        assert row.payload_ciphertext and run.status == "pending_resume"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["pointer", "project", "release", "ciphertext", "missing_payload"],
)
async def test_a_changed_frozen_source_cannot_supply_approval_input(
    fixture, monkeypatch, change
):
    from app.services.approval_inputs import approval_input_digest

    identifier, source = await queued(fixture, monkeypatch)
    async with fixture.sessions() as db:
        run = await db.get(WorkflowExecution, identifier)
        row = await db.get(WorkflowWebhookDelivery, str(source))
        if change == "pointer":
            run.input = {"_webhook_delivery_id": "different-source"}
        elif change == "project":
            run.project_id = "different-project"
        elif change == "release":
            run.release_id = "different-release"
        elif change == "ciphertext":
            row.payload_ciphertext = "invalid-ciphertext"
        else:
            row.status = "cancelled"
            row.payload_ciphertext = None
        await db.flush()
        with pytest.raises(ValueError, match="冻结输入"):
            await approval_input_digest(db, run)
