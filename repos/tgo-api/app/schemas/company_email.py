"""Purpose-specific public account actions; tokens never appear in responses."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=50)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=128, repr=False)


class EmailCodeRequest(EmailRequest):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$", repr=False)


class PasswordResetRequest(ActionRequest):
    password: str = Field(min_length=8, max_length=72, repr=False)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过 72 个 UTF-8 字节")
        return value


class ActionResponse(BaseModel):
    message: str
