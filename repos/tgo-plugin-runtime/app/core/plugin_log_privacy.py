"""Bounded, per-process protection for untrusted plugin diagnostics."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

MASK = "[redacted]"
MAX_LOG_LINE_BYTES = 16_384
OVERSIZED_LINE = "Plugin output line exceeded the log limit"
HIDDEN_OUTPUT = "Plugin output hidden by privacy protection"
_SECRET_NAME = re.compile(
    r"token|secret|pass(?:word|wd)?|credential|auth|cookie|dsn|database_url"
    r"|api[_-]?key|private[_-]?key|(?:^|[_-])key(?:$|[_-])",
    re.I,
)
_PAIR = re.compile(
    r"""(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token"""
    r"""|secret|password|passwd|pwd|authorization|cookie|credentials|jwt"""
    r"""|client_secret|session(?:_id)?|手机号|身份证|地址|姓名|phone|email"""
    r"""|address)["']?\s*[:=]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n]+)""",
)
_URL = re.compile(
    r"\b(?:https?|postgresql(?:\+\w+)?|mysql|redis|amqps?)://" r"[^\s<>\"']+",
    re.I,
)
_AUTH = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.I)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_IDENTITY = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_NUMBER = re.compile(r"(?<!\d)(?:\+?\d[ -]?){8,}\d(?!\d)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _private_url(match: re.Match[str]) -> str:
    try:
        parsed = urlsplit(match.group())
        authority = parsed.netloc
        if "@" in authority:
            authority = MASK + "@" + authority.rsplit("@", 1)[1]
        return urlunsplit(
            (
                parsed.scheme,
                authority,
                parsed.path,
                MASK if parsed.query else "",
                MASK if parsed.fragment else "",
            )
        )
    except ValueError:
        return MASK


@dataclass(frozen=True)
class PluginLogPrivacy:
    values: tuple[str, ...] = field(default=(), repr=False)
    blocked: bool = False
    _tokens: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        tokens: set[str] = set()
        for value in self.values:
            for part in [value, *value.splitlines()]:
                if not part or part.isspace():
                    continue
                tokens.update(
                    {
                        part,
                        json.dumps(part)[1:-1],
                        json.dumps(part, ensure_ascii=False)[1:-1],
                        quote(part, safe=""),
                        quote_plus(part, safe=""),
                    }
                )
        object.__setattr__(
            self,
            "_tokens",
            tuple(sorted(tokens, key=lambda value: (-len(value), value))),
        )

    def extend(
        self,
        config: Mapping[str, object],
        environment: Mapping[str, str],
    ) -> "PluginLogPrivacy":
        if self.blocked:
            return self
        values = set(self.values)

        def add(value: object, *, force: bool = False) -> bool:
            if not isinstance(value, str) or not value.strip():
                return True
            if not force and len(value) < 6:
                return True
            if len(value) > 8192:
                return False
            values.add(value)
            return len(values) <= 128 and sum(map(len, values)) <= 32_768

        for name, value in environment.items():
            if _SECRET_NAME.search(name) and not add(value, force=True):
                return PluginLogPrivacy(blocked=True)
        runtime = config.get("runtime")
        if isinstance(runtime, Mapping):
            configured = runtime.get("env")
            if isinstance(configured, Mapping):
                for name, value in configured.items():
                    force = isinstance(name, str) and bool(
                        _SECRET_NAME.search(name)
                    )
                    if not add(value, force=force):
                        return PluginLogPrivacy(blocked=True)
            arguments = runtime.get("args")
            if isinstance(arguments, list):
                previous = ""
                for argument in arguments:
                    if not isinstance(argument, str):
                        continue
                    if not add(
                        argument, force=bool(_SECRET_NAME.search(previous))
                    ):
                        return PluginLogPrivacy(blocked=True)
                    if "=" in argument:
                        name, value = argument.split("=", 1)
                        if not add(
                            value, force=bool(_SECRET_NAME.search(name))
                        ):
                            return PluginLogPrivacy(blocked=True)
                    previous = argument if argument.startswith("-") else ""
        if values == set(self.values):
            return self
        return PluginLogPrivacy(values=tuple(sorted(values)))

    def sanitize(self, text: str) -> str:
        if self.blocked:
            return HIDDEN_OUTPUT
        if len(text) > MAX_LOG_LINE_BYTES:
            return OVERSIZED_LINE
        text = _CONTROL.sub("", _ANSI.sub("", text))
        text = text.replace("\r", " ").replace("\n", " ")
        for token in self._tokens:
            text = text.replace(token, MASK)
        text = _URL.sub(_private_url, text)
        text = _PAIR.sub(lambda match: match[1] + MASK, text)
        text = _AUTH.sub(lambda match: match[1] + " " + MASK, text)
        text = _JWT.sub(MASK, text)
        text = _EMAIL.sub(MASK, text)
        text = _IDENTITY.sub(MASK, text)
        return _NUMBER.sub(MASK, text)
