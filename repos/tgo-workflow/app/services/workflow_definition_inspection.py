"""Read saved graph metadata without trusting its JSON container shapes."""

from pydantic import JsonValue, ValidationError

from app.schemas.workflow import (
    NodeVariables,
    WorkflowInputParameter,
    WorkflowVariable,
)


class WorkflowDefinitionReadError(ValueError):
    """The stored definition cannot provide a reliable metadata contract."""


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        raise WorkflowDefinitionReadError()
    objects: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise WorkflowDefinitionReadError()
        objects.append(item)
    return objects


def definition_nodes(
    definition: dict[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    return _objects(definition.get("nodes", []))


def node_data(node: dict[str, JsonValue]) -> dict[str, JsonValue]:
    value = node.get("data", {})
    if not isinstance(value, dict):
        raise WorkflowDefinitionReadError()
    return value


def input_parameters(
    definition: dict[str, JsonValue],
    *,
    batch_defaults: bool = False,
) -> list[WorkflowInputParameter]:
    """Keep the first input node and each caller's original defaults."""
    for node in definition_nodes(definition):
        if node.get("type") != "input":
            continue
        variables = _objects(node_data(node).get("input_variables", []))
        result: list[WorkflowInputParameter] = []
        for variable in variables:
            values = variable
            if batch_defaults:
                values = {
                    "name": variable.get("name", ""),
                    "type": variable.get("type", "string"),
                    "description": variable.get("description"),
                    "required": variable.get("required", False),
                    "default": variable.get("default"),
                }
            try:
                result.append(WorkflowInputParameter.model_validate(values))
            except ValidationError:
                raise WorkflowDefinitionReadError() from None
        return result
    return []


_OUTPUTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "webhook": (
        ("body", "object", "Webhook request body"),
        ("params", "object", "Webhook query parameters"),
        ("headers", "object", "Webhook request headers"),
    ),
    "timer": (("timestamp", "string", "Execution timestamp"),),
    "event": (("data", "object", "Event data"),),
    "llm": (("text", "string", "LLM output text"),),
    "agent": (("text", "string", "Agent output text"),),
    "tool": (("result", "any", "Tool execution result"),),
    "api": (
        ("body", "object", "API response body"),
        ("status_code", "number", "HTTP status code"),
        ("headers", "object", "API response headers"),
    ),
    "classifier": (
        ("category_id", "string", "Matched category ID"),
        ("category_name", "string", "Matched category name"),
    ),
}


def available_variables(
    definition: dict[str, JsonValue],
) -> list[NodeVariables]:
    result: list[NodeVariables] = []
    for node in definition_nodes(definition):
        kind = node.get("type")
        data = node_data(node)
        try:
            if kind == "input":
                outputs = [
                    WorkflowVariable.model_validate(
                        {
                            "name": variable.get("name"),
                            "type": variable.get("type"),
                            "description": variable.get("description"),
                        }
                    )
                    for variable in _objects(data.get("input_variables", []))
                ]
            else:
                outputs = [
                    WorkflowVariable(name=name, type=kind, description=detail)
                    for name, kind, detail in _OUTPUTS.get(
                        kind if isinstance(kind, str) else "",
                        (),
                    )
                ]
            if outputs:
                result.append(
                    NodeVariables.model_validate(
                        {
                            "node_id": node.get("id"),
                            "reference_key": data.get("reference_key"),
                            "node_type": kind,
                            "node_label": data.get("label"),
                            "outputs": outputs,
                        }
                    )
                )
        except ValidationError:
            raise WorkflowDefinitionReadError() from None
    return result
