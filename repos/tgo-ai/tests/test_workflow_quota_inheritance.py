"""Nested workflows inherit the original reply's quota, never service secrets."""
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.runtime.tools import utils
from app.services.quota_authorization import (
    UsageAuthorization,
    current_authorization,
)
from tests.test_workflow_production_entry import Response


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_project", [False, True])
async def test_workflow_borrows_original_reply_permit(
    monkeypatch, wrong_project
):
    project = uuid4()
    permit = UsageAuthorization(
        project_id=uuid4() if wrong_project else project,
        reservation_id=uuid4(),
        lease_id=uuid4(),
    )
    session = Response(None)
    session.get = Mock(
        side_effect=[
            Response(
                [
                    {
                        "id": "owned",
                        "name": "查询",
                        "status": "active",
                        "published_version": 1,
                    }
                ]
            ),
            Response(
                {
                    "workflow_id": "owned",
                    "release_version": 1,
                    "release_id": "release",
                    "definition_digest": "a" * 64,
                    "input_parameters": [],
                }
            ),
        ]
    )
    session.post = Mock(
        return_value=Response({"success": True, "output": "accepted"})
    )
    monkeypatch.setattr(
        utils.aiohttp, "ClientSession", Mock(return_value=session)
    )
    monkeypatch.setattr(utils, "verify_current_binding", AsyncMock())
    token = current_authorization.set(permit)
    try:
        tools = await utils.create_workflow_tools(
            "https://workflow.example.test",
            ["owned"],
            str(project),
            agent_id=str(uuid4()),
            request_id="owned-ai-run",
            fixed_versions={"owned": 1},
            release_ids={"owned": "release"},
            release_digests={"owned": "a" * 64},
        )
        result = json.loads(
            await tools[0].entrypoint({"question": "synthetic"})
        )
    finally:
        current_authorization.reset(token)
    if wrong_project:
        assert result["success"] is False
        session.post.assert_not_called()
    else:
        headers = session.post.call_args.kwargs["headers"]
        assert headers["X-AI-Reservation"] == str(permit.reservation_id)
        assert headers["X-AI-Lease"] == str(permit.lease_id)
        assert "X-SaaS-Service-Token" not in headers
        assert "reservation_id" not in session.post.call_args.kwargs["json"]
