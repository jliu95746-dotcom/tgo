"""Keep private chat capabilities on the API, independently of storage type."""

import re
from urllib.parse import urlsplit

PRIVATE_CHAT_FILE_PATH = re.compile(
    r"^(?:/api)?(/v1/chat/files/"
    r"[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}/?)$",
    re.IGNORECASE,
)


def resolve_private_chat_file_url(url: str, api_base_url: str) -> str | None:
    """Normalize API-relative and loopback links without changing the token.

    None means that legacy object URL resolution should handle the input.
    An absolute external URL is preserved even if its path resembles ours.
    """
    parsed = urlsplit(url)
    match = PRIVATE_CHAT_FILE_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return url
    result = api_base_url.rstrip("/") + match.group(1)
    if parsed.query:
        result += "?" + parsed.query
    if parsed.fragment:
        result += "#" + parsed.fragment
    return result
