"""
Validate declared business inputs before creating or running an execution.
"""

from copy import deepcopy
from math import isfinite
from typing import Literal

from pydantic import BaseModel, JsonValue, ValidationError

from app.schemas.node import InputVariable


class InputIssue(BaseModel):
    field: str
    code: Literal["required", "type", "length", "range", "choice"]
    message: str


class WorkflowInputError(ValueError):
    def __init__(self, issues: list[InputIssue]) -> None:
        self.issues = issues
        # Neither HTTP responses nor Worker logs contain rejected input values.
        super().__init__("工作流输入未通过校验，请检查必填项和类型。")


def declared_inputs(definition: dict[str, JsonValue]) -> list[InputVariable]:
    nodes = definition.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("工作流输入定义无效，请检查输入节点。")
    variables: list[InputVariable] = []
    names: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "input":
            continue
        data = node.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("工作流输入定义无效，请检查输入节点。")
        declarations = data.get("input_variables", [])
        if not isinstance(declarations, list):
            raise ValueError("工作流输入定义无效，请检查输入节点。")
        for declaration in declarations:
            try:
                variable = InputVariable.model_validate(declaration)
            except ValidationError:
                raise ValueError(
                    "输入参数声明无效，请检查名称、类型、必填设置和约束范围。"
                ) from None
            if (
                not variable.name.strip()
                or variable.name != variable.name.strip()
            ):
                raise ValueError("输入参数名称不能为空或包含首尾空格。")
            if variable.name in names:
                raise ValueError("输入参数名称重复，请使用不同名称。")
            names.add(variable.name)
            variables.append(variable)
    return variables


def input_configuration_errors(definition: dict[str, JsonValue]) -> list[str]:
    try:
        declared_inputs(definition)
    except ValueError as error:
        return [str(error)]
    return []


def validate_inputs(
    definition: dict[str, JsonValue],
    inputs: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    issues: list[InputIssue] = []
    for variable in declared_inputs(definition):
        value = inputs.get(variable.name)
        missing = value is None or (
            isinstance(value, str) and not value.strip()
        )
        if variable.required and missing:
            issues.append(
                InputIssue(
                    field=variable.name,
                    code="required",
                    message="请填写必填参数。",
                )
            )
            continue
        if value is None:
            continue
        valid = {
            "string": isinstance(value, str),
            "number": (
                isinstance(value, int) and not isinstance(value, bool)
            ) or (isinstance(value, float) and isfinite(value)),
            "boolean": isinstance(value, bool),
        }[variable.type]
        if not valid:
            issues.append(
                InputIssue(
                    field=variable.name,
                    code="type",
                    message="参数类型不正确。",
                )
            )
            continue
        if isinstance(value, str) and (
            (
                variable.min_length is not None
                and len(value) < variable.min_length
            )
            or (
                variable.max_length is not None
                and len(value) > variable.max_length
            )
        ):
            issues.append(
                InputIssue(
                    field=variable.name,
                    code="length",
                    message="文本长度不符合要求。",
                )
            )
        if (
            isinstance(value, str)
            and variable.allowed_values is not None
            and value not in variable.allowed_values
        ):
            issues.append(
                InputIssue(
                    field=variable.name,
                    code="choice",
                    message="请选择预设的业务选项。",
                )
            )
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (
                (variable.minimum is not None and value < variable.minimum)
                or (variable.maximum is not None and value > variable.maximum)
            )
        ):
            issues.append(
                InputIssue(
                    field=variable.name,
                    code="range",
                    message="数字超出允许范围。",
                )
            )
    if issues:
        raise WorkflowInputError(issues)
    # Existing additional context keys are preserved for older callers.
    return deepcopy(inputs)
