"""A stored policy digest cannot stand in for a current connection's ack."""

import pytest
from pydantic import ValidationError

from app.schemas.device_script_policy import (
    ScriptPolicy,
    ScriptPolicyAck,
    ScriptPolicyApply,
    ScriptSessionStatus,
)


def policy():
    return ScriptPolicy(
        protocol_version=1,
        revision=3,
        script_id="daily_report",
        grant_digest="a" * 64,
        timeout_seconds=20,
        max_output_bytes=4096,
        max_runs=2,
        digest=(
            "c67ba11a5189a797b80e7e00fc943219"
            "c91bd270aba8929db2227b4d388fe057"
        ),
    )


def test_apply_and_ack_require_a_connection_binding():
    value = policy()
    request = ScriptPolicyApply(session_id="a" * 32, policy=value)
    ack = ScriptPolicyAck(
        protocol_version=1,
        session_id=request.session_id,
        revision=value.revision,
        digest=value.digest,
    )
    assert ack.session_id == request.session_id
    with pytest.raises(ValidationError):
        ScriptPolicyAck(
            protocol_version=1, revision=value.revision, digest=value.digest
        )
    for session_id in (None, "", "a" * 31, "a" * 32 + "\n", 1):
        with pytest.raises(ValidationError):
            ScriptPolicyApply(session_id=session_id, policy=value)
    with pytest.raises(ValidationError):
        ScriptPolicyApply(session_id="a" * 32, policy=value, owner="arbitrary")


@pytest.mark.parametrize(
    "state,session_id,valid",
    [
        ("awaiting_policy", "a" * 32, True),
        ("active", "b" * 32, True),
        ("disabled", None, True),
        ("unavailable", None, True),
        ("closed", None, True),
        ("active", None, False),
        ("disabled", "a" * 32, False),
        ("ready", "a" * 32, False),
    ],
)
def test_status_distinguishes_local_readiness_from_business_test(
    state, session_id, valid
):
    values = dict(protocol_version=1, state=state, reason="policy_required")
    if session_id is not None:
        values["session_id"] = session_id
    if valid:
        assert ScriptSessionStatus.model_validate(values).state == state
    else:
        with pytest.raises(ValidationError):
            ScriptSessionStatus.model_validate(values)
