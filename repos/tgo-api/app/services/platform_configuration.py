"""Configuration completeness only; never evidence of a live connection."""

from collections.abc import Mapping


def is_platform_configured(platform_type: str, config: Mapping[str, object] | None) -> bool:
    values = config or {}
    if platform_type == 'website':
        return True
    if platform_type == 'email':
        required_email = ['imap_host', 'imap_username', 'imap_password', 'smtp_host']
        if not values.get('use_same_credentials', True):
            required_email.extend(['smtp_username', 'smtp_password'])
        return all(isinstance(values.get(key), str) and bool(str(values[key]).strip()) for key in required_email)
    required = {
        'wecom': ('corp_id', 'app_secret', 'token', 'encoding_aes_key'),
        'wecom_bot': ('token', 'encoding_aes_key'),
        'telegram': ('bot_token',),
        'slack': ('bot_token', 'app_token'),
        'dingtalk_bot': ('app_key', 'app_secret'),
        'feishu_bot': ('app_id', 'app_secret'),
        'custom': ('callback_url',),
    }.get(platform_type)
    if required is None:
        # Unknown/nested integrations must not be marked ready on arbitrary data.
        return False
    return all(isinstance(values.get(key), str) and bool(str(values[key]).strip()) for key in required)
