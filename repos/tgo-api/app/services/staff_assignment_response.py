"""Validate dispatcher output before it reaches assignment history columns."""

from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from app.services.staff_assignment_guard import AssignmentConfiguration


class LLMStaffAssignmentResult(TypedDict):
    configuration: AssignmentConfiguration | None
    refresh_candidates: bool
    selected_staff_id: UUID | None
    llm_response: str | None
    reasoning: str | None
    scores: dict[str, JsonValue] | None
    model_used: str | None
    prompt_used: str | None


def _storable_text(value: str) -> str:
    """PostgreSQL text cannot contain NUL or unpaired Unicode surrogates."""
    if "\x00" in value:
        raise ValueError("Invalid assignment text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Invalid assignment text") from None
    return value


class _Message(BaseModel):
    model_config = ConfigDict(strict=True)
    content: str


class _Choice(BaseModel):
    model_config = ConfigDict(strict=True)
    message: _Message


class _Completion(BaseModel):
    model_config = ConfigDict(strict=True)
    choices: list[_Choice]


class _Selection(BaseModel):
    model_config = ConfigDict(strict=True)
    selected_staff_id: UUID
    reasoning: str | None = None

    @field_validator("reasoning")
    @classmethod
    def storable_reasoning(cls, value: str | None) -> str | None:
        return _storable_text(value) if value is not None else None


def assignment_response_content(response: object) -> str:
    """Accept the existing completion envelope without coercing objects."""
    envelope = _Completion.model_validate(response)
    if not envelope.choices:
        raise ValueError("Missing assignment choice")
    return _storable_text(envelope.choices[0].message.content)


def assignment_selection(
    content: str,
    candidates: set[UUID],
) -> tuple[UUID, str | None]:
    """Only a listed UUID and optional plain text can influence assignment."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0] not in {"```", "```json"} or lines[-1] != "```":
            raise ValueError("Invalid assignment JSON fence")
        text = "\n".join(lines[1:-1])
    selection = _Selection.model_validate_json(text)
    if selection.selected_staff_id not in candidates:
        raise ValueError("Assignment selected an unavailable candidate")
    return selection.selected_staff_id, selection.reasoning
