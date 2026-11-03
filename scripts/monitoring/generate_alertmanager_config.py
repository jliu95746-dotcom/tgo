"""Create private Prometheus and Alertmanager secrets for a monitored deployment."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


EMAIL_PATTERN = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")


@dataclass
class AlertSettings:
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    alert_email_to: str
    saas_internal_token: str


def _validate(settings: AlertSettings) -> None:
    if (
        not settings.smtp_host
        or any(character.isspace() for character in settings.smtp_host)
        or "://" in settings.smtp_host
        or "/" in settings.smtp_host
        or ":" in settings.smtp_host
    ):
        raise ValueError(
            "SMTP host must be a hostname without scheme, port, or whitespace"
        )
    if not 1 <= settings.smtp_port <= 65535:
        raise ValueError("SMTP port must be between 1 and 65535")
    for field_name in ("smtp_from", "alert_email_to"):
        if not EMAIL_PATTERN.fullmatch(getattr(settings, field_name)):
            raise ValueError(f"{field_name} must be one email address")
    for field_name in ("smtp_username", "smtp_password", "saas_internal_token"):
        value = getattr(settings, field_name)
        if not value or "\n" in value or "\r" in value:
            raise ValueError(f"{field_name} must be nonempty and contain no newline")


def _private_file(path: Path, content: str) -> None:
    # Compose bind-mounts file-backed secrets without remapping ownership. Keep
    # the host directory private, while allowing non-root container users to read.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    if os.name != "nt":
        os.chmod(path, 0o644)


def _quoted_yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_config(output_dir: Path, settings: AlertSettings) -> None:
    """Write a non-overwriting, per-host configuration without echoing secrets."""

    _validate(settings)
    if output_dir.is_symlink():
        raise ValueError("Monitoring output directory cannot be a symlink")
    paths = {
        "alertmanager.yml": output_dir / "alertmanager.yml",
        "saas-internal-token": output_dir / "saas-internal-token",
        "smtp-password": output_dir / "smtp-password",
    }
    for path in paths.values():
        if path.exists():
            raise FileExistsError(f"Configuration already exists: {path}")

    config = (
        "global:\n"
        f"  smtp_smarthost: {_quoted_yaml_scalar(f'{settings.smtp_host}:{settings.smtp_port}')}\n"
        f"  smtp_from: {_quoted_yaml_scalar(settings.smtp_from)}\n"
        f"  smtp_auth_username: {_quoted_yaml_scalar(settings.smtp_username)}\n"
        "  smtp_auth_password_file: /run/secrets/smtp-password\n"
        "  smtp_require_tls: true\n"
        "route:\n"
        "  receiver: operations-email\n"
        "  group_by: [alertname, severity]\n"
        "  group_wait: 30s\n"
        "  group_interval: 5m\n"
        "  repeat_interval: 4h\n"
        "receivers:\n"
        "  - name: operations-email\n"
        "    email_configs:\n"
        f"      - to: {_quoted_yaml_scalar(settings.alert_email_to)}\n"
        "        send_resolved: true\n"
    )

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(output_dir, 0o700)
    created: list[Path] = []
    try:
        for name, content in (
            ("alertmanager.yml", config),
            ("saas-internal-token", settings.saas_internal_token),
            ("smtp-password", settings.smtp_password),
        ):
            _private_file(paths[name], content)
            created.append(paths[name])
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "monitoring",
        help="Private output directory; default is the Git-ignored data/monitoring directory",
    )
    args = parser.parse_args()
    settings = AlertSettings(
        smtp_host=input("SMTP server host: ").strip(),
        smtp_port=int(input("SMTP server port (e.g. 587): ").strip()),
        smtp_username=input("SMTP auth username: ").strip(),
        smtp_password=getpass.getpass("SMTP authorization password: "),
        smtp_from=input("Sender email address: ").strip(),
        alert_email_to=input("Operations recipient email address: ").strip(),
        saas_internal_token=getpass.getpass("Existing SAAS_INTERNAL_TOKEN value: "),
    )
    write_config(args.output_dir, settings)
    print(f"Monitoring configuration written to {args.output_dir}")


if __name__ == "__main__":
    main()
