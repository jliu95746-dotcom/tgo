"""Logging configuration."""

import logging
import os
import json
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger


_error_request_id: ContextVar[str | None] = ContextVar(
    "tgo_error_request_id", default=None
)


def bind_error_request_id(request_id: str) -> None:
    """Correlate the server error log within the current request task."""
    _error_request_id.set(request_id)


class _ASGIExceptionFilter(logging.Filter):
    """Keep server error events without serializing arbitrary exceptions."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and record.msg.startswith(
            "Exception in ASGI application"
        ):
            exception = record.exc_info[1] if record.exc_info else None
            error_type = (
                type(exception).__name__
                if exception is not None
                else "Exception"
            )
            record.__dict__["error_type"] = error_type
            request_id = _error_request_id.get()
            if request_id is not None:
                record.__dict__["request_id"] = request_id
            record.msg = (
                f"ASGI request failed: {error_type}; "
                f"request_id={request_id or 'unavailable'}"
            )
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            record.__dict__.pop("message", None)
        return True


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter that includes extra fields automatically."""

    def add_fields(
        self,
        log_record: dict[str, object],
        record: logging.LogRecord,
        message_dict: dict[str, object],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        # Remove redundant fields added by default
        log_record.pop("levelname", None)
        log_record.pop("name", None)


class StartupFormatter(logging.Formatter):
    """Custom formatter for clean startup messages."""

    def format(self, record: logging.LogRecord) -> str:
        # For startup messages, use clean format without timestamp/logger name
        if hasattr(record, "startup") and record.startup:
            return record.getMessage()
        # For regular messages, use standard format + append extras (if any)
        base = super().format(record)

        # Collect non-standard LogRecord attributes injected via `extra=...`
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
        }
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in standard_attrs and k != "startup"
        }
        if not extras:
            return base

        try:
            extra_json = json.dumps(extras, ensure_ascii=False, default=str)
        except Exception:
            extra_json = str(extras)
        return f"{base} | extra={extra_json}"


def setup_logging() -> None:
    """Set up logging configuration."""
    server_logger = logging.getLogger("uvicorn.error")
    if not any(
        isinstance(item, _ASGIExceptionFilter)
        for item in server_logger.filters
    ):
        server_logger.addFilter(_ASGIExceptionFilter())
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    # Configure console handler - use simple format for readability
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        StartupFormatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # Configure file handler with JSON format (includes all extra fields)
    file_handler = logging.FileHandler("logs/app.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        CustomJsonFormatter(
            fmt="%(timestamp)s %(level)s %(logger)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Suppress verbose third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)


def startup_log(message: str, level: int = logging.INFO) -> None:
    """Log a startup message with clean formatting."""
    logger = logging.getLogger("startup")
    record = logger.makeRecord(logger.name, level, "", 0, message, (), None)
    record.startup = True  # Mark as startup message
    logger.handle(record)
