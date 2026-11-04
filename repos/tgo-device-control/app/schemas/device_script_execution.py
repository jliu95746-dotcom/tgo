"""Bound execution/receipt contracts containing typed business values only."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Annotated, Literal, Self

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

BusinessValue = StrictBool | StrictInt | StrictStr
Digest = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]
ConnectionID = Annotated[
    str, Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
]
ExecutionID = Annotated[
    str,
    Field(
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
            r"-[0-9a-f]{4}-[0-9a-f]{12}$"
        ),
    ),
]
ScriptOutcome = Literal[
    "succeeded",
    "failed",
    "canceled",
    "timed_out",
    "output_exceeded",
    "rejected",
    "start_failed",
    "unknown",
    "not_found",
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


class _ReceiptIdentity(_StrictWire):
    protocol_version: int = Field(ge=1, le=1)
    execution_id: ExecutionID
    policy_digest: Digest
    parameters_digest: Digest
    context_digest: Digest


class ScriptReceiptQuery(_ReceiptIdentity):
    session_id: ConnectionID


class ScriptExecuteRequest(ScriptReceiptQuery):
    timeout_seconds: int = Field(ge=1, le=120)
    parameters: dict[str, BusinessValue] = Field(max_length=16)


class ScriptCancellationAck(ScriptReceiptQuery):
    signaled: bool


class ScriptWireReceipt(_ReceiptIdentity):
    status: ScriptOutcome
    failure: str = Field(
        default="", max_length=64, pattern=r"^([a-z][a-z0-9_]{0,63})?$"
    )
    replayed: bool
    fields: dict[str, BusinessValue] = Field(
        default_factory=dict, max_length=32
    )

    @model_validator(mode="after")
    def no_partial_fields(self) -> Self:
        if (self.status == "succeeded") != bool(self.fields):
            raise ValueError(
                "Receipt fields must be a validated successful result"
            )
        if len(canonical_script_values(self.fields)) > 65536:
            raise ValueError("Receipt fields exceed the wire budget")
        return self
