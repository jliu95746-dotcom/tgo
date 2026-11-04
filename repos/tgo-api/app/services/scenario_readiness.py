"""Check resource dependencies without executing business actions."""

import asyncio
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.schemas.scenario_readiness import (
    DeviceToolNames,
    ReadinessIssue,
    ScenarioModelBinding,
    ScenarioReadinessReport,
    ScenarioReadinessRequest,
)
from app.schemas.scenario_resources import (
    ResourceKind,
    ScenarioResourceCatalog,
)
from app.services.device_control_client import device_control_client
from app.services.scenario_resources import (
    CATALOG_TIMEOUT_SECONDS,
    load_catalog,
)

_required: dict[str, tuple[ResourceKind, ...]] = {
    "knowledge": ("model", "knowledge"),
    "logistics": ("model", "connection"),
    "aftersales": ("model", "knowledge"),
    "assistant": ("model",),
    "files": ("device",),
    "report": ("device",),
    "screen": ("device",),
}
_names = {
    "model": "模型",
    "knowledge": "可用知识",
    "tool": "业务工具",
    "workflow": "已发布工作流",
    "connection": "企业业务连接",
    "device": "在线业务设备",
}
_routes = {
    "model": "/settings",
    "knowledge": "/knowledge",
    "tool": "/ai/tools",
    "workflow": "/ai/workflows",
    "connection": "/ai/tools?tab=connections",
    "device": "/ai/device-control",
}


def build_report(
    catalog: ScenarioResourceCatalog, request: ScenarioReadinessRequest
) -> ScenarioReadinessReport:
    effective = request.bindings.model_copy(deep=True)
    sections = {section.kind: section for section in catalog.sections}
    issues: list[ReadinessIssue] = []
    bindings: dict[ResourceKind, list[str]] = {
        "knowledge": [str(value) for value in effective.knowledge_ids],
        "tool": [str(value) for value in effective.tool_ids],
        "workflow": [str(value) for value in effective.workflow_ids],
        "connection": [str(value) for value in effective.connection_ids],
        "device": [str(value) for value in effective.device_ids],
        "model": [],
    }
    required = set(_required[request.template_id])
    if (
        request.template_id == "assistant"
        and not effective.tool_ids
        and not effective.knowledge_ids
    ):
        required.add("knowledge")
    models = sections.get("model")
    if models is not None and (
        "model" in required or effective.model is not None
    ):
        chosen = [
            row
            for row in models.items
            if row.details.purpose == "chat"
            and (
                row.details.provider_id == effective.model.provider_id
                and row.details.model_id == effective.model.model_id
                if effective.model is not None
                else row.details.is_default
            )
        ]
        bindings["model"] = [row.id for row in chosen]
        if effective.model is None and chosen:
            row = chosen[0]
            if (
                row.details.provider_id is not None
                and row.details.model_id is not None
            ):
                effective.model = ScenarioModelBinding(
                    provider_id=row.details.provider_id,
                    model_id=row.details.model_id,
                )
    for kind, selected in bindings.items():
        if (
            kind not in required
            and not selected
            and not (kind == "model" and request.bindings.model is not None)
        ):
            continue
        section = sections.get(kind)
        if section is None or section.status == "unavailable":
            issues.append(
                ReadinessIssue(
                    kind=kind,
                    code="unavailable",
                    message=f"暂时无法核验{_names[kind]}，请重试。",
                    action="retry",
                    route=_routes[kind],
                )
            )
            continue
        if section.status == "not_supported":
            issues.append(
                ReadinessIssue(
                    kind=kind,
                    code="not_supported",
                    message=f"{_names[kind]}接入尚未完成。",
                    action="configure_resource",
                    route=_routes[kind],
                )
            )
            continue
        if not selected:
            explicit_model = (
                kind == "model" and request.bindings.model is not None
            )
            issues.append(
                ReadinessIssue(
                    kind=kind,
                    code="invalid_binding" if explicit_model else "missing",
                    message=f"所选{_names[kind]}无法使用，请重新选择。"
                    if explicit_model
                    else f"请选择{_names[kind]}。",
                    action="choose_resource",
                    route=_routes[kind],
                )
            )
            continue
        by_id = {row.id: row for row in section.items}
        for resource_id in selected:
            bound_resource = by_id.get(resource_id)
            if bound_resource is None:
                partial = section.status == "partial"
                issues.append(
                    ReadinessIssue(
                        kind=kind,
                        code="unverified" if partial else "invalid_binding",
                        message=f"尚未读取到所选{_names[kind]}，请重新核验。"
                        if partial
                        else f"所选{_names[kind]}已失效或不属于当前企业。",
                        action="retry" if partial else "choose_resource",
                        route=_routes[kind],
                    )
                )
            elif bound_resource.status != "configured":
                issues.append(
                    ReadinessIssue(
                        kind=kind,
                        code="not_ready",
                        message=f"所选{_names[kind]}尚未准备好，请检查处理、审核、发布或在线状态。",
                        action="configure_resource",
                        route=_routes[kind],
                    )
                )
    return ScenarioReadinessReport(
        project_id=catalog.project_id,
        template_id=request.template_id,
        channel=request.channel,
        configuration_ready=not issues,
        effective_bindings=effective,
        issues=issues,
    )


async def check_scenario(
    db: Session, project_id: UUID, request: ScenarioReadinessRequest
) -> ScenarioReadinessReport:
    catalog = await load_catalog(db, project_id, request.channel)
    if catalog.project_id != project_id or catalog.channel != request.channel:
        raise HTTPException(502, "无法核验资源归属，请重试。")
    report = build_report(catalog, request)
    if request.template_id in {"files", "report", "screen"} and not any(
        issue.kind == "device" for issue in report.issues
    ):
        device_id = report.effective_bindings.device_ids[0]
        try:
            response = await asyncio.wait_for(
                device_control_client.get_device_tools(
                    str(device_id), str(project_id)
                ),
                timeout=CATALOG_TIMEOUT_SECONDS,
            )
            names = {
                tool.name
                for tool in DeviceToolNames.model_validate(response).tools
            }
            required = {
                "files": {"fs_read"},
                "report": {"shell_exec"},
                "screen": {"screenshot", "click", "type_text"},
            }[request.template_id]
            if not required.issubset(names):
                report.issues.append(
                    ReadinessIssue(
                        kind="device",
                        code="capability_missing",
                        message="设备未提供此场景所需的工具能力。",
                        action="configure_resource",
                        route=_routes["device"],
                    )
                )
        except Exception:
            report.issues.append(
                ReadinessIssue(
                    kind="device",
                    code="unavailable",
                    message="无法读取设备能力，请确认设备在线后重试。",
                    action="retry",
                    route=_routes["device"],
                )
            )
        report.configuration_ready = not report.issues
    return report
