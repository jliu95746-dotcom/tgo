"""Public registration accepts identity details, never tenant or role authority."""

from pydantic import ConfigDict, EmailStr, Field, field_validator

from app.schemas.base import BaseSchema


class RegistrationRequest(BaseSchema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    username: EmailStr = Field(max_length=50)
    password: str = Field(min_length=8, max_length=128)
    nickname: str | None = Field(default=None, max_length=100)
    project_name: str | None = Field(default=None, max_length=255)
    verification_code: str | None = Field(
        default=None, min_length=6, max_length=6, pattern=r"^[0-9]{6}$", repr=False
    )

    @field_validator("username")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value

    @field_validator("nickname", "project_name")
    @classmethod
    def validate_display_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()
