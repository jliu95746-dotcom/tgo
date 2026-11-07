"""Email service schemas."""

from typing import Optional

from pydantic import Field, field_validator

from app.schemas.base import BaseSchema


class EmailConnectionTestRequest(BaseSchema):
    """Schema for testing email server connection."""

    # SMTP Configuration
    smtp_host: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="SMTP server hostname or IP address",
        examples=["smtp.gmail.com", "smtp.office365.com"],
    )
    smtp_port: int = Field(
        ...,
        ge=1,
        le=65535,
        description="SMTP server port (common: 25, 465, 587)",
        examples=[587, 465, 25],
    )
    smtp_username: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="SMTP authentication username (usually email address)",
        examples=["user@example.com"],
    )
    smtp_password: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="SMTP authentication password",
    )
    smtp_use_tls: bool = Field(
        default=True,
        description=(
            'Whether to use TLS/STARTTLS for SMTP connection (recommended '
            'for ports 587, 25)'
        ),
    )

    # IMAP Configuration
    imap_host: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="IMAP server hostname or IP address",
        examples=["imap.gmail.com", "outlook.office365.com"],
    )
    imap_port: int = Field(
        ...,
        ge=1,
        le=65535,
        description="IMAP server port (common: 143, 993)",
        examples=[993, 143],
    )
    imap_username: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="IMAP authentication username (usually email address)",
        examples=["user@example.com"],
    )
    imap_password: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="IMAP authentication password",
    )
    imap_use_ssl: bool = Field(
        default=True,
        description=(
            'Whether to use SSL for IMAP connection (recommended for port '
            '993)'
        ),
    )

    @field_validator('smtp_port', 'imap_port')
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port number is in valid range."""
        if not 1 <= v <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        return v


class EmailConnectionTestResponse(BaseSchema):
    """Schema for email connection test results."""

    smtp_status: str = Field(
        ...,
        description="SMTP connection test status",
        examples=["success", "failed"],
    )
    smtp_message: str = Field(
        ...,
        description="SMTP connection test detailed message or error",
        examples=[
            "SMTP connection successful",
            "Authentication failed: Invalid credentials",
        ],
    )
    imap_status: str = Field(
        ...,
        description="IMAP connection test status",
        examples=["success", "failed"],
    )
    imap_message: str = Field(
        ...,
        description="IMAP connection test detailed message or error",
        examples=["IMAP connection successful", "Connection timeout"],
    )
    overall_success: bool = Field(
        ..., description="Whether both SMTP and IMAP connections succeeded"
    )
