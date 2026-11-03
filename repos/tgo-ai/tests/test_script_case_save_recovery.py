"""Save recovery reads exact actor-owned metadata without private bodies."""

from uuid import uuid4

import pytest

from app.exceptions import NotFoundError
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,  # noqa: F401
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401
from tests.test_agent_device_script_cases import command, service, PRIVATE
from tests.test_agent_device_script_cases import evidence_key  # noqa: F401


@pytest.mark.asyncio
async def test_original_case_save_can_be_recovered_only_in_its_owned_scope(
    trial_db,  # noqa: F811
):
    sessions, project, actor, agent, _, owner, binding, _, _ = trial_db
    async with sessions() as db:
        cases = service(db, owner)
        request = command(binding)
        saved = await cases.save(project, actor, agent, request)
        recovered = await cases.by_request(
            project, actor, agent, request.request_id
        )
        assert recovered.id == saved.id
        assert PRIVATE not in recovered.model_dump_json()
        for other_project, other_actor, other_agent, other_request in (
            (project, uuid4(), agent, request.request_id),
            (uuid4(), actor, agent, request.request_id),
            (project, actor, uuid4(), request.request_id),
            (project, actor, agent, uuid4()),
        ):
            with pytest.raises(NotFoundError):
                await cases.by_request(
                    other_project, other_actor, other_agent, other_request
                )
    assert owner.posts == 0
