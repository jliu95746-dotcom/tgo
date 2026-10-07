"""Configuration completeness only; never evidence of a live connection."""

from collections.abc import Mapping


def is_platform_configured(
    platform_type: str, config: Mapping[str, object] | None,
) -> bool:
    values = config or {}
    if platform_type == 'website':
        return True
    if platform_type == 'email':
        required_email = [
            'imap_host', 'imap_username', 'imap_password', 'smtp_host',
        ]
        if not values.get('use_same_credentials', True):
            required_email.extend(['smtp_username', 'smtp_password'])
        return all(
            isinstance(values.get(key), str) and bool(str(values[key]).strip())
            for key in required_email
        )
    if platform_type == 'wecom':
        required_wecom = ('corp_id', 'token', 'encoding_aes_key')
        has_kf_secret = any(
            isinstance(values.get(key), str) and bool(str(values[key]).strip())
            for key in ('kf_secret', 'app_secret')
        )
        return has_kf_secret and all(
            isinstance(values.get(key), str) and bool(str(values[key]).strip())
            for key in required_wecom
        )
    required = {
        'wecom_bot': ('token', 'encoding_aes_key'),
        'telegram': ('bot_token',),
        'slack': ('bot_token', 'app_token'),
        'dingtalk_bot': ('app_key', 'app_secret'),
        'feishu_bot': ('app_id', 'app_secret'),
        'custom': ('callback_url',),
    }.get(platform_type)
    if required is None:
        # Unknown integrations must not be marked ready on arbitrary data.
        return False
    return all(
        isinstance(values.get(key), str) and bool(str(values[key]).strip())
        for key in required
    )
