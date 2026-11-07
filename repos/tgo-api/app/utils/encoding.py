"""Encoding utilities for identifiers.

Provides a Base62 encoder for channel_id generation.

We avoid external dependencies; Base62 is implemented by treating input bytes as a big integer and converting to a Base62 alphabet.
This maintains a deterministic, reversible mapping for the same input string.
"""
from __future__ import annotations

import zlib
from typing import Final, Union
from uuid import UUID

_BASE62_ALPHABET: Final[
    str
] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
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
    int_value = (
        int.from_bytes(data, byteorder="big", signed=False) if data else 0
    )
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


VISITOR_CHANNEL_SUFFIX: Final[str] = "-vtr"
PROJECT_STAFF_CHANNEL_SUFFIX: Final[str] = "-prj"


def build_project_staff_channel_id(project_id: Union[str, UUID]) -> str:
    """
    Build the WuKongIM project staff channel ID.

    The identifier format is `{project_uuid}-prj`.
    This channel is used for broadcasting messages to all staff in a project.
    """
    if isinstance(project_id, UUID):
        project_str = str(project_id)
    else:
        project_str = str(project_id)
    return f"{project_str}{PROJECT_STAFF_CHANNEL_SUFFIX}"


def build_visitor_channel_id(visitor_id: Union[str, UUID]) -> str:
    """
    Build the WuKongIM customer service channel ID for a visitor.

    The identifier format is `{visitor_uuid}-vtr`.
    """
    if isinstance(visitor_id, UUID):
        visitor_str = str(visitor_id)
    else:
        visitor_str = str(visitor_id)
    return f"{visitor_str}{VISITOR_CHANNEL_SUFFIX}"


def parse_visitor_channel_id(channel_id: str) -> UUID:
    """
    Extract the visitor UUID from a customer service channel ID.

    Raises ValueError when the channel_id is not in the expected format.
    """
    if not isinstance(channel_id, str) or not channel_id.endswith(
        VISITOR_CHANNEL_SUFFIX
    ):
        raise ValueError("Invalid visitor channel_id format")

    uuid_part = channel_id[: -len(VISITOR_CHANNEL_SUFFIX)]
    try:
        return UUID(uuid_part)
    except Exception as exc:
        raise ValueError("Invalid visitor channel_id value") from exc


def get_session_id(from_uid: str, to_uid: str, channel_type: int) -> str:
    """
    Generate a deterministic session ID for a conversation.
    
    If channel_type is 1 (personal), follows the ordering logic based on CRC32 
    hashes of both UIDs to ensure consistency regardless of who is the sender.
    
    This matches WuKongIM's internal session ID generation logic for personal channels.
    """
    if channel_type == 1:
        # For personal channels, session_id is always {uid1}@{uid2} where uid1 and uid2
        # are ordered deterministically to ensure both participants see the same session.
        from_hash = zlib.crc32(from_uid.encode())
        to_hash = zlib.crc32(to_uid.encode())

        if from_hash > to_hash:
            return f"{from_uid}@{to_uid}"

        if from_uid != to_uid and from_hash == to_hash:
            # Hash collision (rare but possible with CRC32)
            # In this case we fallback to string comparison for deterministic ordering
            if from_uid > to_uid:
                return f"{from_uid}@{to_uid}"

        return f"{to_uid}@{from_uid}"

    # For group or other channel types, use the channel_id and channel_type
    return f"{to_uid}@{channel_type}"
