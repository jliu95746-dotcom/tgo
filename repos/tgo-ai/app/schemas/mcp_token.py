"""Validated, non-displayable token for an internal MCP connection."""

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


class MCPAccessToken(BaseModel):
    """Only retain the credential needed by the Authorization header."""

    model_config = ConfigDict(
        strict=True, extra="ignore", hide_input_in_errors=True,
    )

    access_token: SecretStr

    @field_validator("access_token", mode="before")
    @classmethod
    def validate_header_token(cls, value: object) -> str:
        if (
            not isinstance(value, str)
            or not value
            or any(
                ord(character) < 33 or ord(character) > 126
                for character in value
            )
        ):
            raise ValueError("Invalid MCP access token")
        return value
