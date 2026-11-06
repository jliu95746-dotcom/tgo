"""File-related service logic."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import quote


def sanitize_filename(name: str, limit: int = 100) -> str:
    """Sanitize filename for safe storage."""
    name = name.replace("\\", "_").replace("/", "_").replace("..", ".")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if len(name) <= limit:
        return name
    if "." in name:
        base, ext = name.rsplit(".", 1)
        base = base[: max(1, limit - len(ext) - 1)]
        return f"{base}.{ext}"
    return name[:limit]


def get_safe_ascii_filename(
    original_name: str, fallback_id: str = "file"
) -> str:
    """Get a safe ASCII filename for Content-Disposition header."""
    safe_name = original_name or "file"
    safe_name = safe_name.replace("\r", " ").replace("\n", " ").strip()

    normalized_name = unicodedata.normalize("NFKD", safe_name)
    ascii_name_bytes = normalized_name.encode("ascii", "ignore")
    ascii_name = ascii_name_bytes.decode("ascii") if ascii_name_bytes else ""
    ascii_name = "".join(
        ch if ch.isascii() and ch not in {'"', "\\", ";", ","} else "_"
        for ch in ascii_name
    ).strip()

    suffix = Path(safe_name).suffix
    if not ascii_name:
        return f"{fallback_id}{suffix}"
    if suffix and not ascii_name.lower().endswith(suffix.lower()):
        return f"{ascii_name}{suffix}"
    return ascii_name
