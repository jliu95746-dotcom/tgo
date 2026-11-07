"""Identity and message boundaries of the personal WeChat desktop bridge."""

import re


def is_filehelper_identity(value: str | None) -> bool:
    return bool(
        value and re.fullmatch(r"wechat-local:[a-f0-9]{16}:filehelper", value)
    )


def is_scoped_identity(value: str | None) -> bool:
    return bool(
        value
        and re.fullmatch(
            r"wechat-local:[a-f0-9]{16}:(private|group):[a-f0-9]{64}", value
        )
    )


def is_wechat_local_source(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"wxlocal:[a-f0-9]{64}", value))
