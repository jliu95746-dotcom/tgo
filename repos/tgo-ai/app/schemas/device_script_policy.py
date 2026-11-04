"""Reviewed script metadata without local paths or source code."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_INTEGER = 9007199254740991


def _safe_text(value: str) -> bool:
    return all(
        unicodedata.category(char) not in {"Cc", "Cs"}
        and char not in "\ufffd\u2028\u2029"
        for char in value
    )


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScriptParameter(_WireModel):
    name: str
    kind: Literal["string", "integer", "boolean"]
    required: bool
    minimum: int | None = Field(
        default=None, ge=-_SAFE_INTEGER, le=_SAFE_INTEGER
    )
    maximum: int | None = Field(
        default=None, ge=-_SAFE_INTEGER, le=_SAFE_INTEGER
    )
    max_length: int = Field(default=0, ge=0, le=4096)
    choices: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError("invalid business field name")
        return value

    @model_validator(mode="after")
    def typed_constraints(self) -> Self:
        if self.kind == "integer":
            if (
                self.minimum is None
                or self.maximum is None
                or self.minimum > self.maximum
                or self.max_length != 0
                or self.choices
            ):
                raise ValueError("invalid integer bounds")
        elif self.kind == "string":
            if (
                self.max_length < 1
                or self.minimum is not None
                or self.maximum is not None
            ):
                raise ValueError("invalid text bounds")
            if len(set(self.choices)) != len(self.choices) or any(
                not _safe_text(v) or len(v) > self.max_length
                for v in self.choices
            ):
                raise ValueError("invalid text choices")
        elif (
            self.minimum is not None
            or self.maximum is not None
            or self.max_length != 0
            or self.choices
        ):
            raise ValueError(
                "boolean fields cannot carry text or number constraints"
            )
        return self


class _ScriptLimits(_WireModel):
    timeout_seconds: int = Field(ge=1, le=120)
    max_output_bytes: int = Field(ge=256, le=65536)
    max_runs: int = Field(ge=1, le=100)


class ScriptDescriptor(_ScriptLimits):
    script_id: str
    title: str = Field(min_length=1, max_length=100)
    version: str
    grant_digest: str
    effect: Literal["read_only", "write"]
    parameters: list[ScriptParameter] = Field(max_length=16)
    result_fields: list[ScriptParameter] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def valid_definition(self) -> Self:
        if (
            not _NAME.fullmatch(self.script_id)
            or not _VERSION.fullmatch(self.version)
            or not _SHA256.fullmatch(self.grant_digest)
            or not _safe_text(self.title)
        ):
            raise ValueError("invalid reviewed script descriptor")
        for fields in (self.parameters, self.result_fields):
            if len({field.name for field in fields}) != len(fields):
                raise ValueError("duplicate business field")
        return self


class ScriptCatalog(_WireModel):
    protocol_version: int = Field(ge=1, le=1)
    scripts: list[ScriptDescriptor] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_scripts(self) -> Self:
        if len({script.script_id for script in self.scripts}) != len(
            self.scripts
        ):
            raise ValueError("duplicate approved script id")
        return self


class ScriptPolicy(_ScriptLimits):
    protocol_version: int = Field(ge=1, le=1)
    revision: int = Field(ge=1, le=2147483647)
    script_id: str
    grant_digest: str
    digest: str

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        if (
            not _NAME.fullmatch(self.script_id)
            or not _SHA256.fullmatch(self.grant_digest)
            or self.digest != script_policy_digest(self)
        ):
            raise ValueError("invalid approved script policy")
        return self


def script_policy_digest(policy: ScriptPolicy) -> str:
    """Canonical UTF-8/LF decimal fields shared with the Go device client."""
    payload = (
        f"tgo-approved-script-policy-v1\n{policy.protocol_version}\n"
        f"{policy.revision}\n{policy.script_id}\n{policy.grant_digest}\n"
        f"{policy.timeout_seconds}\n{policy.max_output_bytes}\n"
        f"{policy.max_runs}\n"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
