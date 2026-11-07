"""Controlled plugin failures never contain subprocess or exception data."""

from app.core.logging import get_logger

INSTALL_FAILED = "Plugin installation failed"
UPGRADE_FAILED = "Plugin upgrade failed"
START_FAILED = "Plugin startup failed"

_PUBLIC_FAILURES = frozenset(
    {
        INSTALL_FAILED,
        UPGRADE_FAILED,
        START_FAILED,
        "Invalid plugin ID",
        "Reserved plugin ID",
        "Plugin path must be relative",
        "Unsafe plugin path",
        "Plugin paths cannot follow links or junctions",
        "Plugin path escaped its root",
        "Plugin virtual environment interpreter is missing",
        "Plugin ID is not available for this project",
        "Plugin already installed; use upgrade instead",
        "No source configuration provided",
        "download failed",
        "Git clone failed",
        "Go build failed",
        "Python venv creation failed",
        "Python pip install failed",
        "npm install failed",
        "Source path was not found in repository",
        "Unsupported build language",
        "Missing Go build configuration",
        "Missing Python build configuration",
        "Missing Node.js build configuration",
        "Python interpreter is unavailable",
        "System error: 'git' command not found. "
        "Please ensure git is installed in the runtime environment.",
        "System error: 'go' compiler not found. "
        "Please ensure Go is installed.",
        "System error: 'pip' not found in virtual environment.",
        "System error: 'npm' command not found. "
        "Please ensure Node.js is installed.",
        "Plugin installation directory is missing",
        "Plugin binary was not found",
        "No entrypoint or binary found for plugin",
        "Plugin exited before registration",
        "Plugin did not register before the startup timeout",
        "Upgrade configuration has a different plugin ID",
        "Plugin not found",
        "Existing upgrade backup requires recovery",
        "Original installation is missing; upgrade aborted",
        "Plugin could not be stopped; files were preserved",
    }
)

logger = get_logger("services.plugin_failures")


def public_plugin_failure(message: str, fallback: str = INSTALL_FAILED) -> str:
    """Only exact, controlled messages can cross a lifecycle boundary."""
    return message if message in _PUBLIC_FAILURES else fallback


def public_registration_failure(error: Exception) -> str:
    """Return declared reasons without publishing validator details."""
    reason = error.args[0] if error.args else None
    if isinstance(reason, str) and reason in {
        "Invalid dev_token",
        "Invalid dev_token type",
        "Debug connection requires dev_token",
    }:
        return reason
    return "Plugin registration failed"


def log_plugin_failure(operation: str, error: Exception) -> None:
    logger.error(
        "Plugin operation failed",
        extra={"operation": operation, "error_type": type(error).__name__},
    )
