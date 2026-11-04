"""A monitored deployment keeps its email credentials outside Git."""

import tempfile
import unittest
import os
from pathlib import Path

from scripts.monitoring.generate_alertmanager_config import (
    AlertSettings,
    write_config,
)


def sample_settings() -> AlertSettings:
    return AlertSettings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="sender@example.com",
        smtp_password="synthetic-password",
        smtp_from="sender@example.com",
        alert_email_to="ops@example.com",
        saas_internal_token="synthetic-shared-service-token",
    )


class GenerateAlertmanagerConfigTests(unittest.TestCase):
    def test_writes_private_config_and_separate_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_config(output, sample_settings())
            config = (output / "alertmanager.yml").read_text(encoding="utf-8")
            self.assertIn('to: "ops@example.com"', config)
            self.assertIn("smtp_auth_password_file: /run/secrets/smtp-password", config)
            self.assertNotIn("synthetic-password", config)
            self.assertNotIn("synthetic-shared-service-token", config)
            self.assertEqual(
                (output / "saas-internal-token").read_text(encoding="utf-8"),
                "synthetic-shared-service-token",
            )
            self.assertEqual(
                (output / "smtp-password").read_text(encoding="utf-8"),
                "synthetic-password",
            )

    def test_existing_configuration_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_config(output, sample_settings())
            with self.assertRaises(FileExistsError):
                write_config(output, sample_settings())

    def test_invalid_recipient_and_newline_in_host_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            invalid_recipient = sample_settings()
            invalid_recipient.alert_email_to = "not-an-address"
            with self.assertRaises(ValueError):
                write_config(output, invalid_recipient)
            invalid_host = sample_settings()
            invalid_host.smtp_host = "smtp.example.com\nother: value"
            with self.assertRaises(ValueError):
                write_config(output, invalid_host)
            self.assertEqual(list(output.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not Windows ACLs")
    def test_host_directory_is_private_but_mounted_files_are_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "monitoring"
            write_config(output, sample_settings())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for name in ("alertmanager.yml", "saas-internal-token", "smtp-password"):
                self.assertEqual((output / name).stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
