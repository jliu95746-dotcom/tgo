import re
from typing import Any, Dict, Optional

class ExecutionContext:
    def __init__(self, initial_inputs: Optional[Dict[str, Any]] = None, project_id: Optional[str] = None):
        self.data: Dict[str, Any] = initial_inputs or {}
        self.project_id = project_id

    def get_variable(self, path: str) -> Any:
        """
        Get variable value by path like 'start_1.user_input'
        """
        # For now, it's a flat dict of 'reference_key.var_name'
        return self.data.get(path)

    def set_variable(self, reference_key: str, var_name: str, value: Any):
        """
        Set variable value
        """
        self.data[f"{reference_key}.{var_name}"] = value

    def set_node_outputs(self, reference_key: str, outputs: Dict[str, Any]):
        """
        Set multiple outputs for a node
        """
        for key, value in outputs.items():
            self.set_variable(reference_key, key, value)

    def get_expression_context(self) -> dict[str, object]:
        """Expose node namespaces to expressions while preserving flat lookups."""
        names: dict[str, object] = dict(self.data)
        namespaces: dict[str, dict[str, object]] = {}
        for path, value in self.data.items():
            if "." in path:
                reference_key, variable = path.split(".", 1)
                namespaces.setdefault(reference_key, {})[variable] = value
        names.update(namespaces)
        return names

    def resolve_template(self, template: str) -> str:
        """
        Resolve {{reference_key.var_name}} in a string
        """
        if not template or not isinstance(template, str):
            return template

        def replace(match):
            path = match.group(1).strip()
            val = self.get_variable(path)
            return str(val) if val is not None else match.group(0)

        return re.sub(r"\{\{([^}]+)\}\}", replace, template)

    def resolve_variables(self, value: Any) -> Any:
        """
        Recursively resolve variables in dicts, lists, or strings
        """
        if isinstance(value, str):
            return self.resolve_template(value)
        elif isinstance(value, list):
            return [self.resolve_variables(item) for item in value]
        elif isinstance(value, dict):
            return {k: self.resolve_variables(v) for k, v in value.items()}
        return value

