"""Read-only scheduler checks, separate from functional acceptance."""

from __future__ import annotations

import argparse
import hmac
import json
import re
import shlex
import subprocess
from typing import cast

ROLES = ("tgo-workflow", "tgo-workflow-worker", "tgo-workflow-beat")
CONFIGURATION_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "WORKFLOW_QUEUE_NAME",
    "SECRET_KEY",
    "WORKFLOW_APPROVAL_KEYS",
    "WORKFLOW_APPROVAL_ACTIVE_KEY_ID",
    "API_INTERNAL_SERVICE_URL",
)


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError("inspection_invalid")
    return cast(dict[str, object], value)


def command_tokens(config: dict[str, object]) -> list[str]:
    command = config.get("Cmd")
    if not isinstance(command, list) or not all(
        isinstance(arg, str) for arg in command
    ):
        raise ValueError("inspection_invalid")
    return [token for arg in command for token in shlex.split(arg)]


def environment(config: dict[str, object]) -> dict[str, str]:
    raw = config.get("Env")
    if not isinstance(raw, list):
        raise ValueError("inspection_invalid")
    result: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, str) or "=" not in entry:
            raise ValueError("inspection_invalid")
        key, value = entry.split("=", 1)
        if key in result and result[key] != value:
            raise ValueError("inspection_invalid")
        result[key] = value
    return result


def report(status: str, issues: list[str]) -> dict[str, object]:
    return {
        "scope": "workflow_scheduler_deployment_presence",
        "status": status,
        "issues": issues,
        "functional_acceptance_required": True,
    }


def validate_inspections(
    inspections: object, project: str
) -> dict[str, object]:
    if not isinstance(inspections, list):
        return report("failed", ["inspection_invalid"])
    selected: dict[str, list[dict[str, object]]] = {role: [] for role in ROLES}
    try:
        for value in inspections:
            row = mapping(value)
            labels = mapping(mapping(row.get("Config")).get("Labels"))
            role = labels.get("com.docker.compose.service")
            if (
                labels.get("com.docker.compose.project") == project
                and isinstance(role, str)
                and role in selected
            ):
                selected[role].append(row)
        issues = [
            role + "_missing_or_duplicated"
            for role in ROLES
            if len(selected[role]) != 1
        ]
        if issues:
            return report("failed", issues)
        rows = [selected[role][0] for role in ROLES]
        identities = [row.get("Id") for row in rows]
        if not all(
            isinstance(identifier, str) and identifier
            for identifier in identities
        ) or len(set(identities)) != len(ROLES):
            raise ValueError("inspection_invalid")
        images = [row.get("Image") for row in rows]
        if not all(
            isinstance(image, str)
            and re.fullmatch(r"sha256:[a-f0-9]{64}", image)
            for image in images
        ):
            raise ValueError("inspection_invalid")
        if len(set(images)) != 1:
            issues.append("workflow_image_mismatch")
        configs = [mapping(row.get("Config")) for row in rows]
        for role, row in zip(ROLES, rows):
            state = mapping(row.get("State"))
            health = mapping(state.get("Health"))
            if (
                state.get("Status") != "running"
                or health.get("Status") != "healthy"
            ):
                issues.append(role + "_not_healthy")
        beat = command_tokens(configs[-1])
        valid_app = (
            any(
                beat[index : index + 2]
                in (
                    ["-A", "celery_app.celery"],
                    ["--app", "celery_app.celery"],
                )
                for index in range(len(beat))
            )
            or "--app=celery_app.celery" in beat
        )
        if not {"celery", "beat"}.issubset(beat) or not valid_app:
            issues.append("beat_command_invalid")
        worker = command_tokens(configs[1])
        if any(arg in {"-B", "--beat", "beat"} for arg in worker):
            issues.append("duplicate_embedded_beat")
        environments = [environment(config) for config in configs]
        for key in CONFIGURATION_KEYS:
            values = [
                env.get(
                    key, "workflow" if key == "WORKFLOW_QUEUE_NAME" else ""
                )
                for env in environments
            ]
            optional = key.startswith("WORKFLOW_APPROVAL_")
            if (not values[0] and not optional) or not all(
                hmac.compare_digest(values[0].encode(), value.encode())
                for value in values[1:]
            ):
                issues.append("workflow_configuration_mismatch:" + key)
        keys = environments[0].get("WORKFLOW_APPROVAL_KEYS", "")
        active = environments[0].get("WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "")
        if bool(keys) != bool(active):
            issues.append("approval_keyring_incomplete")
        elif (
            not keys
            and len(environments[0].get("SECRET_KEY", "").encode("utf-8")) < 32
        ):
            issues.append("approval_fallback_unavailable")
        return report("failed" if issues else "passed", issues)
    except (ValueError, TypeError):
        return report("failed", ["inspection_invalid"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", args.project):
        parser.error("invalid Compose project name")
    try:
        found = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=com.docker.compose.project=" + args.project,
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        identifiers = found.stdout.split()
        if not all(
            re.fullmatch(r"[a-f0-9]{12,64}", identifier)
            for identifier in identifiers
        ):
            raise ValueError("invalid container identifiers")
        if identifiers:
            result = subprocess.run(
                ["docker", "inspect", *identifiers],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            inspections = json.loads(result.stdout)
        else:
            inspections = []
        outcome = validate_inspections(inspections, args.project)
    except (OSError, ValueError, subprocess.SubprocessError):
        outcome = report("unavailable", ["docker_inspection_unavailable"])
    print(json.dumps(outcome, ensure_ascii=False))
    return (
        0
        if outcome["status"] == "passed"
        else 2
        if outcome["status"] == "unavailable"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
