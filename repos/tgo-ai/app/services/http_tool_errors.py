"""Model-visible HTTP diagnostics must not include URLs or upstream payloads."""
import httpx


def http_tool_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP execution failed with status {exc.response.status_code}"
    elif isinstance(exc, httpx.TimeoutException):
        detail = "HTTP execution failed: Timeout"
    elif isinstance(exc, httpx.RequestError):
        detail = f"HTTP execution failed: {type(exc).__name__}"
    else:
        detail = "HTTP execution failed: internal tool error"
    return f"<error>{detail}</error>"
