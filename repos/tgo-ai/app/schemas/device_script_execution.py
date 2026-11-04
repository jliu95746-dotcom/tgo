"""Bound execution/receipt contracts containing typed business values only."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.schemas.device_script_policy import ScriptParameter, _safe_text
from app.schemas.device_script_policy import ScriptDescriptor, ScriptPolicy
from app.schemas.device_session import DeviceExecutionIdentity

BusinessValue = StrictBool | StrictInt | StrictStr
Digest = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}")


def canonical_script_values(values: Mapping[str, BusinessValue]) -> bytes:
    """Match Go encoding/json UTF-8 map order, control and HTML escaping."""
    encoded = json.dumps(
        dict(values), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    for char, escaped in (
        ("&", r"\u0026"),
        ("<", r"\u003c"),
        (">", r"\u003e"),
        ("\u2028", r"\u2028"),
        ("\u2029", r"\u2029"),
    ):
        encoded = encoded.replace(char, escaped)
    return encoded.encode("utf-8")


def script_input_digest(
    grant_digest: str, values: Mapping[str, BusinessValue]
) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", grant_digest) is None:
        raise ValueError("Invalid reviewed grant digest")
    payload = (
        b"tgo-approved-script-input-v1\n"
        + grant_digest.encode("ascii")
        + b"\n"
        + canonical_script_values(values)
    )
    return hashlib.sha256(payload).hexdigest()


def validate_script_values(
    fields: list[ScriptParameter],
    values: Mapping[str, object],
    *,
    input: bool = True,
) -> dict[str, BusinessValue]:
    validated: dict[str, BusinessValue] = {}
    if set(values) - {field.name for field in fields}:
        raise ValueError("Unknown reviewed business field")
    for field in fields:
        if field.name not in values:
            if field.required:
                raise ValueError("Required business field is missing")
            continue
        value = values[field.name]
        if field.kind == "integer":
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or field.minimum is None
                or field.maximum is None
                or not field.minimum <= value <= field.maximum
            ):
                raise ValueError("Business integer is outside reviewed bounds")
        elif field.kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError("Business boolean has an invalid type")
        elif field.kind == "string":
            if (
                not isinstance(value, str)
                or len(value) > field.max_length
                or field.required
                and value == ""
                or "\x00" in value
                or any(unicodedata.category(char) == "Cs" for char in value)
                or input
                and not _safe_text(value)
                or field.choices
                and value not in field.choices
            ):
                raise ValueError("Business text is outside reviewed bounds")
        else:
            raise ValueError("Unsupported business field")
        if not isinstance(value, (str, int, bool)):
            raise ValueError("Unsupported business value")
        validated[field.name] = value
    if len(canonical_script_values(validated)) > 65536:
        raise ValueError("Business values exceed the wire budget")
    return validated


class _StrictWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScriptExecuteBody(_StrictWire):
    parameters: dict[str, BusinessValue] = Field(max_length=16)

    @model_validator(mode="after")
    def bounded_parameters(self) -> Self:
        if any(not _NAME.fullmatch(name) for name in self.parameters):
            raise ValueError("Invalid business field name")
        if len(canonical_script_values(self.parameters)) > 65536:
            raise ValueError("Business values exceed the wire budget")
        return self


class ScriptPolicyRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    project_id: UUID
    policy: ScriptPolicy
    status: Literal["pending", "active", "revoked"]
    verified: Literal[False] = False
    updated_at: datetime
    revoked_at: datetime | None = None


class ScriptExecutionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    execution_id: UUID
    session_id: UUID
    agent_id: UUID
    workflow_execution_id: UUID | None = None
    policy_digest: Digest
    parameters_digest: Digest
    context_digest: Digest
    status: Literal[
        "unknown",
        "succeeded",
        "failed",
        "canceled",
        "timed_out",
        "output_exceeded",
        "rejected",
        "start_failed",
    ]
    failure: str = Field(max_length=64, pattern=r"^([a-z][a-z0-9_]{0,63})?$")
    created_at: datetime
    finished_at: datetime | None = None
    result_available: StrictBool = False
    fields: dict[str, BusinessValue] = Field(
        default_factory=dict, max_length=32
    )

    @model_validator(mode="after")
    def confirmed_fields(self) -> Self:
        if bool(self.fields) != self.result_available or (
            self.result_available and self.status != "succeeded"
        ):
            raise ValueError("Unconfirmed receipt contains business fields")
        return self


@dataclass(frozen=True)
class ScriptReceiptReference:
    """Immutable query identity without private inputs or POST authority."""

    identity: DeviceExecutionIdentity
    execution_id: UUID
    policy_digest: str
    parameters_digest: str
    context_digest: str
    timeout_seconds: int
    max_output_bytes: int
    descriptor_json: str = dataclass_field(repr=False)


@dataclass(frozen=True)
class PreparedScriptCall(ScriptReceiptReference):
    """Only an in-memory prepared call owns the private execution body."""

    parameters_json: str = dataclass_field(repr=False)


def validate_script_receipt(
    call: ScriptReceiptReference, result: ScriptExecutionRead
) -> ScriptExecutionRead:
    receipt = ScriptExecutionRead.model_validate_json(result.model_dump_json())
    identity = call.identity
    item = ScriptDescriptor.model_validate_json(call.descriptor_json)
    if (
        receipt.device_id != identity.device_id
        or receipt.execution_id != call.execution_id
        or receipt.session_id != identity.session_id
        or receipt.agent_id != identity.agent_id
        or receipt.workflow_execution_id != identity.workflow_execution_id
        or receipt.policy_digest != call.policy_digest
        or receipt.parameters_digest != call.parameters_digest
        or receipt.context_digest != call.context_digest
    ):
        raise ValueError("Script receipt identity mismatch")
    if receipt.result_available:
        fields = validate_script_values(
            item.result_fields, receipt.fields, input=False
        )
        if len(canonical_script_values(fields)) > call.max_output_bytes:
            raise ValueError("Reviewed result budget exceeded")
    return receipt


def validate_reviewed_script_configuration(
    project: UUID,
    device: UUID,
    bound: ScriptPolicyRead,
    descriptor: ScriptDescriptor,
) -> tuple[ScriptPolicyRead, ScriptDescriptor]:
    """Use identical metadata checks for configuration and actual execution."""
    owned = ScriptPolicyRead.model_validate_json(bound.model_dump_json())
    item = ScriptDescriptor.model_validate_json(descriptor.model_dump_json())
    policy = owned.policy
    if (
        owned.project_id != project
        or owned.device_id != device
        or owned.status != "active"
        or owned.revoked_at is not None
        or item.effect != "read_only"
        or item.script_id != policy.script_id
        or item.grant_digest != policy.grant_digest
        or policy.timeout_seconds > item.timeout_seconds
        or policy.max_output_bytes > item.max_output_bytes
        or policy.max_runs > item.max_runs
    ):
        raise ValueError("Reviewed device scope changed")
    return owned, item
