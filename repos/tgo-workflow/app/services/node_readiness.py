"""Configuration checks for nodes supported by the current execution path."""
from collections.abc import Mapping, Sequence
import re
from typing import cast
from uuid import UUID
from app.config import settings
import app.engine.nodes  # noqa: F401 - register executors before checking availability
from app.engine.nodes.api import api_node_is_read_only, api_node_target_error
from app.engine.nodes.registry import get_executor_class
from app.services.business_query_condition import business_query_condition_error
from app.services.aftersales_review import ACTION_NAME, CASE_CATEGORIES
from app.services.parallel_topology import parallel_plan_for
from app.services.schedule_rules import ScheduleRuleError, parse_schedule
from app.schemas.capabilities import WorkflowCapabilities, WorkflowNodeCapability

SUPPORTED_NODE_TYPES = frozenset({"input", "event", "answer", "agent", "approval", "tool", "api", "llm", "condition", "classifier", "business_query", "parallel"})
REQUIRED_NODE_FIELDS = {"event": ("event_type",), "agent": ("agent_id",), "approval": ("approver_staff_id", "summary"), "tool": ("tool_id",), "llm": ("provider_id", "model_id"), "api": ("url",), "business_query": ("connection_id", "operation", "order_variable")}
KNOWN_NODE_TYPES = ("input", "timer", "webhook", "event", "agent", "approval", "llm", "condition", "parallel", "classifier", "tool", "api", "business_query", "answer")
_PREVIEW_VARIABLE = re.compile(r"\{\{([^{}]+)\}\}")


def _approval_preview_errors(
    summary: str,
    label: str,
    references: set[str],
    input_names: dict[str, set[str]],
) -> list[str]:
    errors: list[str] = []
    unparsed = _PREVIEW_VARIABLE.sub("", summary)
    if "{{" in unparsed or "}}" in unparsed:
        return [f"Node '{label}' 审批预览包含未闭合的变量标记"]
    for match in _PREVIEW_VARIABLE.finditer(summary):
        path = match.group(1).strip()
        reference, separator, name = path.partition(".")
        if not separator or not reference or not name or reference not in references:
            errors.append(f"Node '{label}' 审批预览引用了不存在或尚未执行的变量")
        elif reference in input_names and input_names[reference] and name not in input_names[reference]:
            errors.append(f"Node '{label}' 审批预览引用了未声明的输入参数")
    return errors


def _contains_raw_api_output(value: object, raw_paths: set[str]) -> bool:
    if isinstance(value, str):
        return value.strip() in raw_paths or any(
            match.group(1).strip() in raw_paths
            for match in _PREVIEW_VARIABLE.finditer(value)
        )
    if isinstance(value, Mapping):
        return any(
            _contains_raw_api_output(item, raw_paths)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_raw_api_output(item, raw_paths) for item in value)
    return False


def _node_supported(kind: str) -> bool:
    enabled = kind in SUPPORTED_NODE_TYPES or (
        kind == "timer" and settings.TIMER_EXECUTION_ENABLED
    ) or (
        kind == "webhook" and settings.WEBHOOK_EXECUTION_ENABLED
    )
    return enabled and get_executor_class(kind) is not None


def workflow_capabilities() -> WorkflowCapabilities:
    return WorkflowCapabilities(
        nodes=[WorkflowNodeCapability(
            type=kind,
            available=_node_supported(kind),
            preparation_available=(
                kind == "webhook" and get_executor_class(kind) is not None
            ),
            required_fields=list(REQUIRED_NODE_FIELDS.get(kind, ())),
            reason=None if _node_supported(kind)
            else "当前执行引擎尚不支持此能力。",
        ) for kind in KNOWN_NODE_TYPES],
        execution_modes=["draft_test", "production"],
    )


def node_configuration_errors(
    nodes: Sequence[Mapping[str, object]],
    edges: Sequence[Mapping[str, object]] | None = None,
    *, allow_timer_preparation: bool = False,
    allow_webhook_preparation: bool = False,
) -> list[str]:
    errors: list[str] = []
    kinds = {str(node.get("type", "")) for node in nodes}
    known_references: set[str] = set()
    references_by_node: dict[str, str] = {}
    input_names: dict[str, set[str]] = {}
    input_declarations: list[Mapping[str, object]] = []
    raw_api_paths: set[str] = set()
    for node in nodes:
        raw_data = node.get("data")
        data = raw_data if isinstance(raw_data, Mapping) else {}
        kind = node.get("type")
        if kind == "api":
            runtime_reference = str(data.get("reference_key"))
            raw_api_paths.update({
                f"{runtime_reference}.body", f"{runtime_reference}.headers",
            })
        reference = data.get("reference_key", "input" if kind == "input" else None)
        if not isinstance(reference, str) or not reference:
            continue
        known_references.add(reference)
        node_id = node.get("id")
        if isinstance(node_id, str):
            references_by_node[node_id] = reference
        declarations = data.get("input_variables")
        if kind == "input" and isinstance(declarations, list):
            input_declarations.extend(
                declaration for declaration in declarations
                if isinstance(declaration, Mapping)
            )
            input_names[reference] = {
                declaration["name"] for declaration in declarations
                if isinstance(declaration, Mapping)
                and isinstance(declaration.get("name"), str)
            }
    parents: dict[str, set[str]] = {}
    if edges is not None:
        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if isinstance(source, str) and isinstance(target, str):
                parents.setdefault(target, set()).add(source)
    if "approval" in kinds and kinds.intersection({"agent", "tool", "api"}):
        errors.append("审批流程当前不能包含 AI 员工、业务工具或 API 节点；动作预览与幂等门禁完成后再开放。")
    if "event" in kinds and kinds.intersection({"agent", "tool", "api", "business_query"}):
        errors.append("内部事件工作流暂不支持员工或外部业务操作节点。")
    if "timer" in kinds and kinds.intersection({
        "agent", "tool", "api", "business_query",
    }):
        errors.append("定时工作流暂不支持员工或外部业务操作节点。")
    if "webhook" in kinds and kinds.intersection({"agent", "tool", "api", "business_query"}):
        errors.append("外部通知工作流暂不支持员工或外部业务操作节点。")
    if "parallel" in kinds and edges is not None:
        for node in nodes:
            if node.get("type") == "parallel" and isinstance(node.get("id"), str):
                try:
                    parallel_plan_for(str(node["id"]), nodes, edges)
                except ValueError as error:
                    errors.append(f"Node '{node['id']}' {error}")
    if "business_query" in kinds and (
        len(input_declarations) != 1
        or input_declarations[0].get("name") != "order_no"
        or input_declarations[0].get("type") != "string"
        or input_declarations[0].get("required") is not True
    ):
        errors.append("受控业务查询只允许一个必填的字符串订单号输入 order_no。")
    ids: set[str] = set()
    references: set[str] = set()
    for node in nodes:
        node_id = str(node.get("id", ""))
        kind = str(node.get("type", ""))
        raw_data = node.get("data", {})
        data = cast(Mapping[str, object], raw_data) if isinstance(raw_data, Mapping) else {}
        label = str(data.get("label") or node_id)
        if not node_id or node_id in ids:
            errors.append(f"Duplicate node or empty node id: {node_id}")
        ids.add(node_id)
        reference = str(data.get("reference_key") or node_id)
        if reference in references:
            errors.append(f"Duplicate reference_key: {reference}")
        references.add(reference)
        unavailable = not _node_supported(kind)
        if unavailable and not (
            (kind == "timer" and allow_timer_preparation)
            or (kind == "webhook" and allow_webhook_preparation)
        ):
            errors.append(f"Node '{label}' type '{kind}' is not supported by the current execution path")
        if kind == "webhook" and (
            data.get("method", "POST") != "POST"
            or data.get("path") not in (None, "")
        ):
            errors.append(f"Node '{label}' 外部触发仅支持 POST 和系统分配的地址。")
        if kind == "timer":
            expression, zone = data.get("cron_expression"), data.get("timezone", "UTC")
            if not isinstance(expression, str) or not isinstance(zone, str):
                errors.append(f"Node '{label}' 定时规则和时区必须是字符串。")
            else:
                try:
                    parse_schedule(expression, zone)
                except ScheduleRuleError as error:
                    errors.append(f"Node '{label}' {error}")
        required = REQUIRED_NODE_FIELDS.get(kind, ())
        for field in required:
            if not isinstance(data.get(field), str) or not str(data.get(field)).strip():
                errors.append(f"Node '{label}' requires {field}")
        event_type = data.get("event_type")
        if kind == "event" and not (
            isinstance(event_type, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", event_type)
        ):
            errors.append(f"Node '{label}' requires a valid event_type")
        if kind == "agent":
            device_id = data.get("expected_device_id")
            policy_digest = data.get("expected_device_policy_digest")
            if device_id is not None or policy_digest is not None:
                try:
                    if not isinstance(device_id, str):
                        raise ValueError("missing device ID")
                    UUID(device_id)
                except ValueError:
                    errors.append(f"Node '{label}' 需要有效的固定设备编号")
                if (not isinstance(policy_digest, str)
                        or re.fullmatch(r"[0-9a-f]{64}", policy_digest) is None):
                    errors.append(f"Node '{label}' 需要有效的设备读取策略摘要")
        if kind == "approval":
            try:
                UUID(str(data.get("approver_staff_id", "")))
            except ValueError:
                errors.append(f"Node '{label}' requires a valid approver_staff_id")
            summary = data.get("summary")
            if isinstance(summary, str) and len(summary) > 500:
                errors.append(f"Node '{label}' summary is too long")
            action = data.get("after_approval_action")
            if "timer" in kinds and action is not None:
                errors.append(f"Node '{label}' 定时审批没有可信客户会话，不能执行客户售后动作")
            if kinds.intersection({"event", "webhook"}) and action is not None:
                errors.append(f"Node '{label}' 事件与外部通知审批没有可信客户会话，不能执行客户售后动作")
            if action is not None and action != ACTION_NAME:
                errors.append(f"Node '{label}' 包含不支持的审批后动作")
            if action == ACTION_NAME:
                if data.get("case_category") not in CASE_CATEGORIES:
                    errors.append(f"Node '{label}' 需要有效的售后核对类别")
                if isinstance(summary, str) and len(summary) > 375:
                    errors.append(f"Node '{label}' 售后核对预览过长")
            elif data.get("case_category") is not None:
                errors.append(f"Node '{label}' 售后核对类别缺少对应动作")
            if isinstance(summary, str):
                visible_references = known_references
                if edges is not None:
                    ancestors = {node_id}
                    pending = [node_id]
                    while pending:
                        for parent in parents.get(pending.pop(), set()) - ancestors:
                            ancestors.add(parent)
                            pending.append(parent)
                    visible_references = {
                        references_by_node[ancestor] for ancestor in ancestors
                        if ancestor in references_by_node
                    }
                errors.extend(_approval_preview_errors(
                    summary, label, visible_references, input_names,
                ))
            minutes = data.get("expires_in_minutes", 1440)
            if type(minutes) is not int or not 1 <= minutes <= 10080:
                errors.append(f"Node '{label}' requires expires_in_minutes from 1 to 10080")
        if kind == "business_query":
            try:
                UUID(str(data.get("connection_id", "")))
            except ValueError:
                errors.append(f"Node '{label}' requires a valid connection_id")
            if data.get("operation") not in ("order_query", "logistics_query"):
                errors.append(f"Node '{label}' requires a supported operation")
            if data.get("order_variable") != "input.order_no" or "order_no" not in input_names.get("input", set()):
                errors.append(f"Node '{label}' requires declared order_variable input.order_no")
            if kinds.intersection({"api", "tool", "agent", "llm", "approval", "parallel"}):
                errors.append("受控业务查询流程只允许输入、条件和回答节点，避免订单信息流入其他工具。")
        if kind == "condition" and "business_query" in kinds:
            condition_error = business_query_condition_error(data)
            if condition_error is not None:
                errors.append(f"Node '{label}' {condition_error}")
        if "approval" in kinds and kind == "llm" and data.get("tool_ids"):
            errors.append("审批流程当前不能让模型调用业务工具；动作预览与幂等门禁完成后再开放。")
        if kind == "llm" and data.get("tool_ids"):
            errors.append(
                f"Node '{label}' 当前不能让模型直接调用工具；"
                "请使用已核验的只读工具节点。"
            )
        if kind == "api" and data.get("url"):
            target_error = api_node_target_error(data["url"])
            if target_error:
                errors.append(f"Node '{label}' {target_error}")
        if kind == "api" and not api_node_is_read_only(data):
            errors.append(f"Node '{label}' currently supports only bodyless GET requests")
        if kind == "answer":
            output_type = str(data.get("output_type") or "variable")
            output_field = {"variable": "output_variable", "template": "output_template", "structured": "output_structure"}.get(output_type)
            if not output_field or not data.get(output_field):
                errors.append(f"Node '{label}' requires an output configuration")
            elif _contains_raw_api_output(data[output_field], raw_api_paths):
                errors.append(
                    f"Node '{label}' 不能直接返回 API 原始响应；"
                    "请使用受控业务连接、字段映射与归属校验。"
                )
            if "business_query" in kinds and output_field and _contains_raw_api_output(
                data.get(output_field), {"input.order_no"},
            ):
                errors.append("受控业务查询不能在回答中直接输出订单号。")
    return errors
