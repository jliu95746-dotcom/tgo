"""Lightweight logging helpers for supervisor runtime."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional


class TransportDetailFilter(logging.Filter):
    """HTTP transport detail logs can contain signed URLs and auth headers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


class StructuredFormatter(logging.Formatter):
    """Custom formatter that displays structured logging fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with structured fields."""
        # Start with the basic formatted message
        base_message = super().format(record)

        # Check if there are extra fields to display
        if hasattr(record, "extra_fields") and record.extra_fields:
            # Format extra fields as key=value pairs
            extra_parts = []
            for key, value in record.extra_fields.items():
                # Handle different value types
                if isinstance(value, (dict, list)):
                    value_str = json.dumps(value)
                elif value is None:
                    value_str = "None"
                else:
                    value_str = str(value)
                extra_parts.append(f"{key}={value_str}")

            # Append extra fields to the message
            if extra_parts:
                return f"{base_message} {' '.join(extra_parts)}"

        return base_message


class BoundLogger:
    """Minimal structured logger with ``bind`` support."""

    def __init__(self, logger: logging.Logger, context: Optional[Dict[str, Any]] = None) -> None:
        self._logger = logger
        self._context: Dict[str, Any] = context or {}

    # ------------------------------------------------------------------
    # Public API
    def bind(self, **kwargs: Any) -> "BoundLogger":
        """Return a new logger with extended context."""
        if not kwargs:
            return self
        merged = {**self._context, **kwargs}
        return BoundLogger(self._logger, merged)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, kwargs, exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    def _log(
        self,
        level: int,
        msg: str,
        kwargs: Dict[str, Any],
        *,
        exc_info: bool = False,
    ) -> None:
        extra_fields: Dict[str, Any] = {**self._context}
        if kwargs:
            extra_fields.update(kwargs)

        if extra_fields:
            self._logger.log(level, msg, extra={"extra_fields": extra_fields}, exc_info=exc_info)
        else:
            self._logger.log(level, msg, exc_info=exc_info)


def setup_logging() -> None:
    """Ensure a basic logging configuration is present."""
    # A filter remains effective even when a root/debug configuration is changed.
    # Application-level tool diagnostics retain status and safe failure category.
    for name in ("httpx", "httpcore.http11", "httpcore.http2", "httpcore.connection", "httpcore.proxy"):
        transport_logger = logging.getLogger(name)
        if not any(isinstance(item, TransportDetailFilter) for item in transport_logger.filters):
            transport_logger.addFilter(TransportDetailFilter())
    if not logging.getLogger().handlers:
        # Create handler with structured formatter
        handler = logging.StreamHandler()
        formatter = StructuredFormatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)


def get_logger(name: Optional[str] = None) -> BoundLogger:
    """Return a :class:`BoundLogger` instance."""
    setup_logging()
    return BoundLogger(logging.getLogger(name))
