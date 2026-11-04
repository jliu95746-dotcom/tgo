"""Custom exception classes for the application."""

import uuid
from typing import Any, Dict, Optional


class TGOAIServiceException(Exception):
    """Base exception for TGO AI Service."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(TGOAIServiceException):
    """Authentication failed."""

    def __init__(
        self,
        message: str = "Authentication failed",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, "AUTHENTICATION_FAILED", details)


class AuthorizationError(TGOAIServiceException):
    """Authorization failed."""

    def __init__(
        self,
        message: str = "Access denied",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, "ACCESS_DENIED", details)


class NotFoundError(TGOAIServiceException):
    """Resource not found."""

    def __init__(
        self,
        resource: str,
        resource_id: Optional[uuid.UUID] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        message = f"{resource} not found"
        if resource_id:
            message += f" (ID: {resource_id})"
        
        error_details = details or {}
        if resource_id:
            error_details["resource_id"] = str(resource_id)
        error_details["resource_type"] = resource
        
        super().__init__(message, f"{resource.upper()}_NOT_FOUND", error_details)


class ValidationError(TGOAIServiceException):
    """Data validation failed."""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        error_details = details or {}
        if field:
            error_details["field"] = field
        
        super().__init__(message, "VALIDATION_ERROR", error_details)


class ConflictError(TGOAIServiceException):
    """Resource conflict."""

    def __init__(
        self,
        message: str,
        resource: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        error_details = details or {}
        if resource:
            error_details["resource_type"] = resource
        
        code = f"{resource.upper()}_CONFLICT" if resource else "CONFLICT"
        super().__init__(message, code, error_details)


class ModelConfigurationVerificationError(ConflictError):
    """The runtime model did not match its authenticated owner proof."""


class DatabaseError(TGOAIServiceException):
    """Database operation failed."""

    def __init__(
        self,
        message: str = "Database operation failed",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, "DATABASE_ERROR", details)


class ExternalServiceError(TGOAIServiceException):
    """External service call failed."""

    def __init__(
        self,
        service: str,
        message: str = "External service call failed",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        error_details = details or {}
        error_details["service"] = service
        
        super().__init__(message, "EXTERNAL_SERVICE_ERROR", error_details)


class RateLimitError(TGOAIServiceException):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, "RATE_LIMIT_EXCEEDED", details)
