"""Release checks compare secrets in memory and never print their values."""
import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.release.preflight_model_configuration import (
    main,
    validate_inspections,
)

RUNNER = "scripts.release.preflight_model_configuration.subprocess.run"


def pair():
    environment = {
        "SAAS_ENABLED": "true",
        "SAAS_BILLING_ENABLED": "true",
        "SECRET_KEY": "synthetic-model-proof-key",
        "SAAS_INTERNAL_TOKEN": "synthetic-quota-token",
    }
    return [
        {
            "Id": "container-" + service,
            "Name": "/" + service,
            "Config": {
                "Env": [
                    key + "=" + value for key, value in environment.items()
                ],
                "Labels": {"com.docker.compose.service": "tgo-" + service},
            },
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
        }
        for service in ["api", "ai"]
    ]


class PreflightTests(unittest.TestCase):
    def report(self, rows, required=True):
        return validate_inspections(
            rows, "api", "ai", require_billing=required
        )

    def test_matching_live_configuration_passes_without_secret_receipts(self):
        result = self.report(pair())
        self.assertEqual(result.status, "passed")
        self.assertNotIn(
            "synthetic-model-proof-key", json.dumps(result.as_dict())
        )
        self.assertNotIn("synthetic-quota-token", repr(result))

    def test_each_mode_or_key_mismatch_blocks_release(self):
        for field, value, issue in [
            ("SAAS_ENABLED", "false", "saas_mode"),
            ("SAAS_BILLING_ENABLED", "false", "billing_mode"),
            ("SECRET_KEY", "different-proof-key", "model_proof_key"),
            (
                "SAAS_INTERNAL_TOKEN",
                "different-token",
                "quota_authorization_key",
            ),
        ]:
            with self.subTest(field=field):
                rows = pair()
                rows[1]["Config"]["Env"] = [
                    item
                    for item in rows[1]["Config"]["Env"]
                    if not item.startswith(field + "=")
                ] + [field + "=" + value]
                result = self.report(rows)
                self.assertEqual(result.status, "failed")
                self.assertIn(issue, result.issues)

    def test_missing_invalid_and_ambiguous_configuration_fails_closed(self):
        for mode in [
            "missing_key",
            "bad_boolean",
            "ambiguous",
            "unhealthy",
            "same_identity",
            "wrong_role",
            "missing_container",
        ]:
            with self.subTest(mode=mode):
                rows = pair()
                if mode == "missing_key":
                    rows[0]["Config"]["Env"] = [
                        value
                        for value in rows[0]["Config"]["Env"]
                        if not value.startswith("SECRET_KEY=")
                    ]
                elif mode == "bad_boolean":
                    rows[0]["Config"]["Env"].append("SAAS_ENABLED=perhaps")
                elif mode == "ambiguous":
                    rows[0]["Config"]["Env"].append(
                        "secret_key=conflicting-value"
                    )
                elif mode == "unhealthy":
                    rows[0]["State"]["Health"]["Status"] = "starting"
                elif mode == "same_identity":
                    rows[1]["Id"] = rows[0]["Id"]
                elif mode == "wrong_role":
                    rows[1]["Config"]["Labels"][
                        "com.docker.compose.service"
                    ] = "tgo-api"
                else:
                    rows.pop()
                self.assertEqual(self.report(rows).status, "failed")

    def test_matching_invalid_boolean_values_still_fail_verification(self):
        rows = pair()
        for row in rows:
            row["Config"]["Env"] = [
                value
                for value in row["Config"]["Env"]
                if not value.startswith(
                    ("SAAS_ENABLED=", "SAAS_BILLING_ENABLED=")
                )
            ] + ["SAAS_ENABLED=perhaps", "SAAS_BILLING_ENABLED=perhaps"]
        result = self.report(rows, required=False)
        self.assertEqual(result.status, "failed")
        self.assertIn("saas_mode", result.issues)
        self.assertIn("billing_mode", result.issues)

    def test_matching_empty_credentials_never_pass(self):
        rows = pair()
        for row in rows:
            row["Config"]["Env"] = [
                "SAAS_ENABLED=true",
                "SAAS_BILLING_ENABLED=true",
                "SECRET_KEY=",
                "SAAS_INTERNAL_TOKEN=",
            ]
        result = self.report(rows)
        self.assertIn("model_proof_key", result.issues)
        self.assertIn("quota_authorization_key", result.issues)

    def test_absent_mode_flags_are_unverified_for_unmetered_installations(
        self,
    ):
        rows = pair()
        for row in rows:
            row["Config"]["Env"] = ["SECRET_KEY=synthetic-model-proof-key"]
        result = self.report(rows, required=False)
        self.assertEqual(result.status, "failed")
        self.assertIn("saas_mode", result.issues)
        self.assertIn("billing_mode", result.issues)

    def test_unmetered_mode_does_not_pass_required_billing(
        self,
    ):
        rows = pair()
        for row in rows:
            row["Config"]["Env"] = [
                "SECRET_KEY=synthetic-model-proof-key",
                "SAAS_ENABLED=false",
                "SAAS_BILLING_ENABLED=false",
            ]
        self.assertEqual(self.report(rows, required=False).status, "passed")
        self.assertEqual(self.report(rows, required=True).status, "failed")

    def test_cli_hides_docker_failures_and_uses_bounded_read_only_inspection(
        self,
    ):
        output = io.StringIO()
        failure = subprocess.CompletedProcess(
            [], 1, "", "synthetic-secret-docker-error"
        )
        with patch(
            RUNNER,
            return_value=failure,
        ) as run, contextlib.redirect_stdout(output):
            code = main(
                [
                    "--api-container",
                    "api",
                    "--ai-container",
                    "ai",
                    "--require-billing",
                ]
            )
        self.assertEqual(code, 2)
        self.assertNotIn("synthetic-secret", output.getvalue())
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertIn("inspect", run.call_args.args[0])
        self.assertNotIn("exec", run.call_args.args[0])

    def test_cli_success_and_unavailable_paths_never_echo_inspected_values(
        self,
    ):
        cases = [
            (
                subprocess.CompletedProcess([], 0, json.dumps(pair()), ""),
                None,
                0,
            ),
            (None, OSError("synthetic-private-error"), 2),
            (
                None,
                subprocess.TimeoutExpired(
                    "docker", 10, output="synthetic-private-error"
                ),
                2,
            ),
            (
                subprocess.CompletedProcess(
                    [], 0, "synthetic-private-error", ""
                ),
                None,
                2,
            ),
        ]
        for response, error, expected in cases:
            with self.subTest(expected=expected):
                output = io.StringIO()
                with patch(
                    RUNNER,
                    return_value=response,
                    side_effect=error,
                ), contextlib.redirect_stdout(output):
                    code = main(
                        [
                            "--api-container",
                            "api",
                            "--ai-container",
                            "ai",
                            "--require-billing",
                        ]
                    )
                self.assertEqual(code, expected)
                self.assertNotIn("synthetic", output.getvalue())
                self.assertIn("scope", json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
