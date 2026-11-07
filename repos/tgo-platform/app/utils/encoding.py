"""Encoding utilities for identifiers.

Provides a Base62 encoder for channel_id generation.

We avoid external dependencies; Base62 is implemented by treating input bytes as a big integer and converting to a Base62 alphabet.
This maintains a deterministic, reversible mapping for the same input string.
"""

from __future__ import annotations

from typing import Final

_BASE62_ALPHABET: Final[str] = (
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_BASE62_BASE: Final[int] = 62


def _int_to_base62(n: int) -> str:
    """Convert a non-negative integer to a base62 string.

    Args:
        n: Non-negative integer
    Returns:
        Base62 representation using [0-9A-Za-z].
    """
    if n == 0:
        return "0"
    digits = []
    base = _BASE62_BASE
    while n > 0:
        n, rem = divmod(n, base)
        digits.append(_BASE62_ALPHABET[rem])
    return "".join(reversed(digits))


def encode_channel_id(raw_string: str) -> str:
    """Encode a channel identifier using Base62.

    Implementation details:
    - The input string is encoded to UTF-8 bytes.
    - The bytes are interpreted as a big-endian integer.
    - The integer is converted to a Base62 string via _int_to_base62.

    Note: This preserves a deterministic mapping and is reversible (if needed) by parsing back to int and to bytes).
    """
    if raw_string is None:
        return "0"
    data = raw_string.encode("utf-8")
    # Interpret bytes as big integer (unsigned)
    int_value = int.from_bytes(data, byteorder="big", signed=False) if data else 0
    return _int_to_base62(int_value)


def _base62_to_int(s: str) -> int:
    """Convert a base62 string to integer.

    Raises ValueError on invalid characters.
    """
    if not s:
        return 0
    n = 0
    base = _BASE62_BASE
    alphabet = _BASE62_ALPHABET
    for ch in s.strip():
        idx = alphabet.find(ch)
        if idx == -1:
            raise ValueError(f"Invalid base62 character: {ch}")
        n = n * base + idx
    return n


def decode_channel_id(encoded: str) -> str:
    """Decode a Base62-encoded channel identifier back to its original string.

    This reverses encode_channel_id().
    """
    try:
        int_value = _base62_to_int(encoded)
    except Exception as exc:
        raise ValueError(f"Invalid base62 string: {encoded}") from exc

    if int_value == 0:
        return ""
    # minimal bytes needed for big-endian representation
    byte_len = (int_value.bit_length() + 7) // 8
    data = int_value.to_bytes(byte_len, byteorder="big", signed=False)
    return data.decode("utf-8")
