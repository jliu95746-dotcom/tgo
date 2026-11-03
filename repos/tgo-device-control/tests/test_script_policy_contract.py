"""Client/server share typed script metadata and execution limits."""
import pytest
from pydantic import ValidationError

from app.schemas.device_script_policy import (
    ScriptCatalog,
    ScriptDescriptor,
    ScriptParameter,
    ScriptPolicy,
    script_policy_digest,
)


def policy_values():
    return dict(
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


def test_policy_contract_matches_the_go_vector():
    values = policy_values()
    policy = ScriptPolicy.model_validate(values)
    assert script_policy_digest(policy) == values["digest"]
    for name, value in [
        ("revision", 4),
        ("script_id", "other_report"),
        ("grant_digest", "b" * 64),
        ("timeout_seconds", 21),
        ("max_output_bytes", 4097),
        ("max_runs", 3),
    ]:
        with pytest.raises(ValidationError):
            ScriptPolicy.model_validate({**values, name: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"revision": 3.0},
        {"max_runs": "2"},
        {"script_id": "daily_report\n"},
        {"command": "anything"},
        {"timeout_seconds": 0},
        {"max_output_bytes": 1000000},
    ],
)
def test_policy_rejects_coercion_and_extra_execution_fields(changes):
    with pytest.raises(ValidationError):
        ScriptPolicy.model_validate({**policy_values(), **changes})


def descriptor():
    return ScriptDescriptor(
        script_id="daily_report",
        title="每日业务报表",
        version="v1",
        grant_digest="a" * 64,
        effect="read_only",
        parameters=[],
        result_fields=[
            ScriptParameter(
                name="count",
                kind="integer",
                required=True,
                minimum=0,
                maximum=10000,
            )
        ],
        timeout_seconds=20,
        max_output_bytes=4096,
        max_runs=2,
    )


def test_catalog_rejects_paths_duplicate_ids_and_unknown_field_types():
    item = descriptor()
    assert ScriptCatalog(protocol_version=1, scripts=[item]).scripts == [item]
    with pytest.raises(ValidationError):
        ScriptCatalog(protocol_version=1, scripts=[item, item])
    with pytest.raises(ValidationError):
        ScriptDescriptor.model_validate(
            {**item.model_dump(), "script_path": "/private/local.py"}
        )
    with pytest.raises(ValidationError):
        ScriptParameter(name="command", kind="object", required=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum": True},
        {"maximum": 1.5},
        {"max_length": 20},
        {"minimum": 10, "maximum": 1},
        {"choices": ["text"]},
    ],
)
def test_integer_metadata_cannot_claim_incompatible_constraints(changes):
    with pytest.raises(ValidationError):
        ScriptParameter.model_validate(
            dict(
                name="count",
                kind="integer",
                required=True,
                minimum=0,
                maximum=10000,
            )
            | changes
        )
