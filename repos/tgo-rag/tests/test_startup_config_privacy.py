"""Startup diagnostics must not print connection or credential values."""

import io
import unittest
from contextlib import redirect_stdout

from src.rag_service.startup_banner import print_config_info


class StartupConfigPrivacyTests(unittest.TestCase):
    def render(self, values: dict[str, object]) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            print_config_info(values)
        return output.getvalue()

    def test_connection_values_are_fully_hidden(self) -> None:
        for name, value in (
            (
                "Database URL",
                "postgresql+asyncpg://synthetic-user:"
                "synthetic-password@host/db",
            ),
            ("Redis URL", "redis://:synthetic-password@host:6379/2"),
            ("Callback Url", "https://host/hook?token=synthetic-query-secret"),
            ("Database URL", "malformed-synthetic-connection-value"),
        ):
            with self.subTest(name=name, value=value):
                output = self.render({name: value})
                self.assertNotIn(value, output)
                self.assertNotIn("synthetic-", output)
                self.assertIn("***", output)
                self.assertIn(name, output)

    def test_secret_values_have_no_prefix_or_suffix_disclosure(self) -> None:
        secret = "synthetic-prefix-secret-suffix"
        for name in (
            "Password", "JWT_SECRET", "API Key", "TOKEN", "Authorization",
            "Credentials",
        ):
            with self.subTest(name=name):
                output = self.render({name: secret})
                self.assertNotIn("synt", output)
                self.assertNotIn("ffix", output)
                self.assertIn("***", output)

    def test_public_diagnostics_remain_readable(self) -> None:
        output = self.render({"Environment": "development", "Port": 18082})
        self.assertIn("development", output)
        self.assertIn("18082", output)

    def test_empty_or_non_string_sensitive_values_remain_hidden(self) -> None:
        for value in (None, "", 123456, {"credential": "synthetic-secret"}):
            with self.subTest(value=value):
                output = self.render({"Database URL": value})
                if value != "":
                    self.assertNotIn(str(value), output)
                self.assertIn("***", output)


if __name__ == "__main__":
    unittest.main()
