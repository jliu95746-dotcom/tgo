"""Read-only API/AI release checks; credentials never enter the report."""
from __future__ import annotations

import argparse
import hmac
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Literal, Mapping, cast


@dataclass(frozen=True)
class Check:
    name: str
    status: Literal["passed", "failed", "not_required"]


@dataclass(frozen=True)
class PreflightReport:
    status: Literal["passed", "failed", "unavailable"]
    checks: tuple[Check, ...] = ()
    issues: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        messages = {
            "passed": "服务模型配置一致性检查通过",
            "failed": "服务配置不一致或无法核验，请停止发布并检查配置",
            "unavailable": "无法读取服务配置，请检查 Docker 连接和容器名称",
        }
        return {
            "scope": "api_ai_model_configuration",
            "status": self.status,
            "message": messages[self.status],
            "checks": [
                {"name": row.name, "status": row.status} for row in self.checks
            ],
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class ServiceSnapshot:
    identity: str
    role: str
    healthy: bool
    configuration: Mapping[str, str] = field(repr=False)


def _mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return cast(dict[str, object], value)
    return None


def _snapshot(value: object) -> ServiceSnapshot | None:
    row = _mapping(value)
    if row is None or not isinstance(row.get("Id"), str) or not row["Id"]:
        return None
    config = _mapping(row.get("Config"))
    state = _mapping(row.get("State"))
    if config is None or state is None:
        return None
    labels = _mapping(config.get("Labels"))
    health = _mapping(state.get("Health"))
    environment = config.get("Env")
    if not isinstance(environment, list) or labels is None:
        return None
    values: dict[str, str] = {}
    for item in environment:
        if not isinstance(item, str) or "=" not in item:
            return None
        key, content = item.split("=", 1)
        key = key.casefold()
        if key in values and values[key] != content:
            return None
        values[key] = content
    role = labels.get("com.docker.compose.service")
    if not isinstance(role, str):
        return None
    return ServiceSnapshot(
        identity=cast(str, row["Id"]),
        role=role,
        healthy=state.get("Status") == "running"
        and health is not None
        and health.get("Status") == "healthy",
        configuration=values,
    )


def _boolean(service: ServiceSnapshot, key: str) -> bool | None:
    configured = service.configuration.get(key)
    if configured is None:
        return None
    value = configured.casefold()
    if value in {"1", "on", "t", "true", "y", "yes"}:
        return True
    if value in {"0", "off", "f", "false", "n", "no"}:
        return False
    return None


def _same_secret(
    left: ServiceSnapshot, right: ServiceSnapshot, key: str
) -> bool:
    first, second = left.configuration.get(key, ""), right.configuration.get(
        key, ""
    )
    return bool(first.strip() and second.strip()) and hmac.compare_digest(
        first.encode("utf-8"), second.encode("utf-8")
    )


def validate_inspections(
    inspections: object,
    api_container: str,
    ai_container: str,
    *,
    require_billing: bool = False,
) -> PreflightReport:
    if not isinstance(inspections, list) or len(inspections) != 2:
        return PreflightReport("failed", issues=("inspection_invalid",))
    selected: dict[str, ServiceSnapshot] = {}
    for value in inspections:
        row = _mapping(value)
        snapshot = _snapshot(value)
        if (
            row is None
            or snapshot is None
            or not isinstance(row.get("Name"), str)
        ):
            return PreflightReport("failed", issues=("inspection_invalid",))
        name = cast(str, row["Name"]).removeprefix("/")
        if name in selected:
            return PreflightReport("failed", issues=("inspection_invalid",))
        selected[name] = snapshot
    api, ai = selected.get(api_container), selected.get(ai_container)
    if api is None or ai is None or api_container == ai_container:
        return PreflightReport("failed", issues=("inspection_invalid",))
    api_saas, ai_saas = _boolean(api, "saas_enabled"), _boolean(
        ai, "saas_enabled"
    )
    api_billing = _boolean(api, "saas_billing_enabled")
    ai_billing = _boolean(ai, "saas_billing_enabled")
    checks: list[Check] = []

    def check(name: str, passed: bool, *, required: bool = True) -> None:
        checks.append(
            Check(
                name,
                "not_required"
                if not required
                else "passed"
                if passed
                else "failed",
            )
        )

    check(
        "service_identity",
        api.identity != ai.identity
        and api.role == "tgo-api"
        and ai.role == "tgo-ai",
    )
    check("container_health", api.healthy and ai.healthy)
    check("saas_mode", api_saas is not None and api_saas == ai_saas)
    check(
        "billing_mode", api_billing is not None and api_billing == ai_billing
    )
    check(
        "billing_requires_saas",
        (api_billing is not True or api_saas is True)
        and (ai_billing is not True or ai_saas is True),
    )
    check(
        "required_billing",
        api_saas is True
        and ai_saas is True
        and api_billing is True
        and ai_billing is True,
        required=require_billing,
    )
    check("model_proof_key", _same_secret(api, ai, "secret_key"))
    metered = (
        api_saas is True
        and ai_saas is True
        and api_billing is True
        and ai_billing is True
    )
    check(
        "quota_authorization_key",
        _same_secret(api, ai, "saas_internal_token"),
        required=metered,
    )
    issues = tuple(row.name for row in checks if row.status == "failed")
    return PreflightReport(
        "failed" if issues else "passed", tuple(checks), issues
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读核对 API/AI 的模型及计费配置")
    parser.add_argument("--api-container", required=True)
    parser.add_argument("--ai-container", required=True)
    parser.add_argument("--require-billing", action="store_true")
    args = parser.parse_args(argv)
    names = [args.api_container, args.ai_container]
    if any(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name) is None
        for name in names
    ):
        parser.error("容器名称格式无效")
    try:
        inspected = subprocess.run(
            ["docker", "inspect", "--type", "container", *names],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if inspected.returncode:
            report = PreflightReport(
                "unavailable", issues=("inspection_unavailable",)
            )
        else:
            report = validate_inspections(
                json.loads(inspected.stdout),
                *names,
                require_billing=args.require_billing,
            )
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        report = PreflightReport(
            "unavailable", issues=("inspection_unavailable",)
        )
    print(json.dumps(report.as_dict(), ensure_ascii=False))
    return {"passed": 0, "failed": 1, "unavailable": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
