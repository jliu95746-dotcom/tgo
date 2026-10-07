"""Select the credential used by WeCom Customer Service APIs."""


def resolve_wecom_kf_secret(kf_secret: object, legacy_app_secret: object) -> str:
    """Prefer the dedicated KF secret while supporting existing KF installations."""
    for value in (kf_secret, legacy_app_secret):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
