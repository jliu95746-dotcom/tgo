"""Bounded versioned event keys; values never appear in diagnostics."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import SecretStr

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


class EventKeyringError(RuntimeError):
    """Unavailable configuration or retained key; never use another key."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise EventKeyringError("工作流事件密钥配置包含重复编号。")
        result[name] = value
    return result


@dataclass(frozen=True)
class EventKeyring:
    active_id: str | None
    keys: Mapping[str, SecretStr] = field(repr=False)
    legacy_secret: SecretStr = field(repr=False)
    configured: bool = False

    def secret(self, key_id: str | None) -> str:
        if key_id is None:
            if self.configured:
                key = self.keys.get("legacy")
            else:
                key = self.legacy_secret
        else:
            key = self.keys.get(key_id)
        if key is None or not key.get_secret_value():
            raise EventKeyringError("工作流事件所需历史密钥不可用。")
        return key.get_secret_value()


def parse_event_keyring(
    raw: SecretStr | None,
    active_id: str | None,
    legacy_secret: str,
) -> EventKeyring:
    encoded = raw.get_secret_value() if raw is not None else ""
    active = active_id or None
    if not encoded and active is None:
        return EventKeyring(None, {}, SecretStr(legacy_secret))
    try:
        if not encoded or len(encoded.encode("utf-8")) > 65536:
            raise ValueError()
        decoded = json.loads(encoded, object_pairs_hook=_pairs)
        if not isinstance(decoded, dict) or not 1 <= len(decoded) <= 8:
            raise ValueError()
        keys: dict[str, SecretStr] = {}
        for name, value in decoded.items():
            if (
                not isinstance(name, str)
                or _KEY_ID.fullmatch(name) is None
                or not isinstance(value, str)
                or not 32 <= len(value.encode("utf-8")) <= 4096
            ):
                raise ValueError()
            keys[name] = SecretStr(value)
        if active is None or active == "legacy" or active not in keys:
            raise ValueError()
        return EventKeyring(active, keys, SecretStr(""), configured=True)
    except (ValueError, TypeError, UnicodeError):
        raise EventKeyringError("工作流事件密钥配置无效。") from None
