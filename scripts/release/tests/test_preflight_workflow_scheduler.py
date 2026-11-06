"""Missing Beat must fail even when API and Worker are healthy."""

import contextlib
import io
import json
import subprocess
import unittest
from copy import deepcopy
from unittest.mock import patch

from scripts.release.preflight_workflow_scheduler import (
    main,
    validate_inspections,
)


def services():
    environment = [
        "DATABASE_URL=synthetic-database",
        "REDIS_URL=synthetic-broker",
        "SECRET_KEY=synthetic-private-key",
        "WORKFLOW_APPROVAL_KEYS=synthetic-approval-keys",
        "WORKFLOW_APPROVAL_ACTIVE_KEY_ID=fixture",
        "API_INTERNAL_SERVICE_URL=synthetic-api",
    ]
    return [
        {
            "Id": "container-" + role,
            "Image": "sha256:" + "a" * 64,
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "fixture",
                    "com.docker.compose.service": role,
                },
                "Env": list(environment),
                "Cmd": command,
            },
        }
        for role, command in [
            ("tgo-workflow", ["uvicorn", "app.main:app"]),
            (
                "tgo-workflow-worker",
                [
                    "sh",
                    "-c",
                    "celery -A celery_app.celery worker --loglevel=info",
                ],
            ),
            (
                "tgo-workflow-beat",
                [
                    "sh",
                    "-c",
                    "exec celery -A celery_app.celery beat --loglevel=info",
                ],
            ),
        ]
    ]


class WorkflowSchedulerPreflightTests(unittest.TestCase):
    def test_supported_shared_secret_fallback_is_accepted(self):
        rows = services()
        for row in rows:
            row["Config"]["Env"] = [
                value
                for value in row["Config"]["Env"]
                if not value.startswith(("WORKFLOW_APPROVAL_", "SECRET_KEY="))
            ] + ["SECRET_KEY=" + "synthetic-fallback-" * 2]
        self.assertEqual(
            validate_inspections(rows, "fixture")["status"], "passed"
        )

    def test_short_fallback_or_incomplete_keyring_is_rejected(self):
        for mode in ("short_fallback", "incomplete_keyring"):
            rows = services()
            for row in rows:
                row["Config"]["Env"] = [
                    value
                    for value in row["Config"]["Env"]
                    if not value.startswith("WORKFLOW_APPROVAL_")
                ]
                if mode == "incomplete_keyring":
                    row["Config"]["Env"].append(
                        "WORKFLOW_APPROVAL_ACTIVE_KEY_ID=fixture"
                    )
            self.assertEqual(
                validate_inspections(rows, "fixture")["status"], "failed"
            )

    def test_matching_services_pass_presence_check(self):
        self.assertEqual(
            validate_inspections(services(), "fixture")["status"], "passed"
        )

    def test_missing_beat_fails_with_healthy_api_and_worker(self):
        result = validate_inspections(services()[:2], "fixture")
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "tgo-workflow-beat_missing_or_duplicated", result["issues"]
        )

    def test_duplicate_beat_fails(self):
        rows = services()
        rows.append(deepcopy(rows[-1]))
        self.assertEqual(
            validate_inspections(rows, "fixture")["status"], "failed"
        )

    def test_stopped_or_unhealthy_scheduler_fails(self):
        for state in (
            {"Status": "exited"},
            {"Status": "running", "Health": {"Status": "unhealthy"}},
        ):
            with self.subTest(state=state):
                rows = services()
                rows[-1]["State"] = state
                self.assertEqual(
                    validate_inspections(rows, "fixture")["status"], "failed"
                )

    def test_foreign_project_does_not_satisfy_scheduler(self):
        rows = services()
        rows[-1]["Config"]["Labels"]["com.docker.compose.project"] = "other"
        self.assertEqual(
            validate_inspections(rows, "fixture")["status"], "failed"
        )

    def test_different_image_fails(self):
        rows = services()
        rows[-1]["Image"] = "sha256:" + "b" * 64
        self.assertIn(
            "workflow_image_mismatch",
            validate_inspections(rows, "fixture")["issues"],
        )

    def test_wrong_celery_application_or_missing_beat_command_fails(self):
        for command in (
            ["celery", "-A", "other.app", "beat"],
            ["sleep", "infinity"],
        ):
            rows = services()
            rows[-1]["Config"]["Cmd"] = command
            self.assertIn(
                "beat_command_invalid",
                validate_inspections(rows, "fixture")["issues"],
            )

    def test_worker_embedded_beat_is_rejected(self):
        rows = services()
        rows[1]["Config"]["Cmd"][-1] += " -B"
        self.assertIn(
            "duplicate_embedded_beat",
            validate_inspections(rows, "fixture")["issues"],
        )

    def test_configuration_drift_fails_without_values_in_report(self):
        for key in (
            "DATABASE_URL",
            "REDIS_URL",
            "WORKFLOW_QUEUE_NAME",
            "SECRET_KEY",
            "WORKFLOW_APPROVAL_KEYS",
            "WORKFLOW_APPROVAL_ACTIVE_KEY_ID",
            "API_INTERNAL_SERVICE_URL",
        ):
            with self.subTest(key=key):
                rows = services()
                rows[-1]["Config"]["Env"] = [
                    value
                    for value in rows[-1]["Config"]["Env"]
                    if not value.startswith(key + "=")
                ]
                rows[-1]["Config"]["Env"].append(
                    key + "=private-changed-value"
                )
                result = validate_inspections(rows, "fixture")
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("private-changed-value", json.dumps(result))

    def test_malformed_inspection_fails(self):
        for value in (
            None,
            {},
            [None],
            [
                {
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": "fixture",
                            "com.docker.compose.service": "tgo-workflow-beat",
                        }
                    }
                }
            ],
        ):
            self.assertEqual(
                validate_inspections(value, "fixture")["status"], "failed"
            )

    def test_docker_failure_reports_unavailable_without_stderr(self):
        with (
            patch(
                "scripts.release.preflight_workflow_scheduler.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1, ["docker"], stderr="private-error"
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(["--project", "fixture"]), 2)
        self.assertNotIn("private-error", output.getvalue())
        self.assertEqual(
            json.loads(output.getvalue())["status"], "unavailable"
        )

    def test_cli_reads_actual_inspection_without_printing_environment(self):
        results = [
            subprocess.CompletedProcess([], 0, stdout="abcdef123456\n"),
            subprocess.CompletedProcess([], 0, stdout=json.dumps(services())),
        ]
        with (
            patch(
                "scripts.release.preflight_workflow_scheduler.subprocess.run",
                side_effect=results,
            ) as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(["--project", "fixture"]), 0)
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("synthetic-private-key", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "passed")
        self.assertTrue(
            json.loads(output.getvalue())["functional_acceptance_required"]
        )

    def test_empty_project_fails_instead_of_inspecting_all_containers(self):
        with (
            patch(
                "scripts.release.preflight_workflow_scheduler.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, stdout=""),
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["--project", "fixture"]), 1)
        self.assertEqual(run.call_count, 1)

    def test_malformed_json_and_timeouts_report_unavailable(self):
        runner = "scripts.release.preflight_workflow_scheduler.subprocess.run"
        failures = [
            subprocess.TimeoutExpired(["docker"], 15),
            [
                subprocess.CompletedProcess([], 0, stdout="abcdef123456\n"),
                subprocess.CompletedProcess([], 0, stdout="not-json"),
            ],
        ]
        for failure in failures:
            with (
                self.subTest(failure=type(failure).__name__),
                patch(runner, side_effect=failure),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(main(["--project", "fixture"]), 2)
            self.assertEqual(
                json.loads(output.getvalue())["status"], "unavailable"
            )


if __name__ == "__main__":
    unittest.main()
