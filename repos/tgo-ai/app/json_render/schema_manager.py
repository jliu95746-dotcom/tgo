"""json-render schema manager for TGO AI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _MODULE_DIR / "schema" / "spec_stream_line.json"
_EXAMPLES_DIR = _MODULE_DIR / "examples"

JSON_RENDER_SPEC_FENCE_OPEN = "```spec"
JSON_RENDER_SPEC_FENCE_CLOSE = "```"


class JsonRenderSchemaManager:
    """Manages json-render schema loading and system prompt generation."""

    def __init__(
        self,
        *,
        schema_path: Optional[Path] = None,
        examples_dir: Optional[Path] = None,
    ) -> None:
        self._schema_path = schema_path or _SCHEMA_PATH
        self._examples_dir = examples_dir or _EXAMPLES_DIR
        self._schema: Optional[dict] = None
        self._examples: Optional[str] = None
        self._load()

    def _load(self) -> None:
        if self._schema_path.exists():
            self._schema = json.loads(
                self._schema_path.read_text(encoding="utf-8")
            )
            logger.info(
                "json-render patch schema loaded", path=str(self._schema_path)
            )
        else:
            logger.warning(
                "json-render patch schema not found",
                path=str(self._schema_path),
            )
        if self._examples_dir.exists():
            parts: List[str] = []
            for p in sorted(self._examples_dir.glob("*.jsonl")):
                name = p.stem
                content = p.read_text(encoding="utf-8").strip()
                parts.append(f"### {name}\n```text\n{content}\n```")
            self._examples = "\n\n".join(parts) if parts else None
            logger.info("json-render examples loaded", count=len(parts))

    @property
    def schema_json(self) -> Optional[str]:
        if self._schema is None:
            return None
        return json.dumps(self._schema, indent=2, ensure_ascii=False)

    def generate_system_prompt(
        self,
        *,
        role_description: str = "",
        workflow_description: str = "",
        ui_description: str = "",
        include_schema: bool = True,
        include_examples: bool = True,
    ) -> str:
        """Assemble the json-render instruction block for the LLM prompt."""
        parts: List[str] = []
        parts.append(_JSON_RENDER_PROTOCOL_INSTRUCTIONS)

        if role_description:
            parts.append(f"## Role\n{role_description}")
        if workflow_description:
            parts.append(f"## Workflow\n{workflow_description}")
        if ui_description:
            parts.append(f"## UI Guidelines\n{ui_description}")
        if include_schema and self._schema is not None:
            parts.append(
                "## json-render Patch Schema\n"
                "Each JSONL patch line MUST validate against this schema.\n"
                f"```json\n{self.schema_json}\n```"
            )
        if include_examples and self._examples:
            parts.append(f"## json-render Examples\n{self._examples}")
        return "\n\n".join(parts)


_JSON_RENDER_PROTOCOL_INSTRUCTIONS = (
    f'## json-render Response Protocol\n'
    f'\n'
    f'When generating rich UI you MUST follow these rules:\n'
    f'\n'
    f'1. First write conversational text.\n'
    f'2. Then output JSONL SpecStream patch lines inside a fenced block:\n'
    f'   - opening fence: '
    f'{JSON_RENDER_SPEC_FENCE_OPEN}'
    f'\n'
    f'   - closing fence: '
    f'{JSON_RENDER_SPEC_FENCE_CLOSE}'
    f'\n'
    f'3. Inside the fence, each line MUST be a single RFC 6902 patch object.\n'
    f'4. Patches MUST build a **complete, self-contained** json-render spec. '
    f"Each message's spec is rendered independently — there is NO "
    f'cross-message state. You MUST always include:\n'
    f'   - `root`: string (REQUIRED — the spec will not render without it)\n'
    f'   - `elements`: object map of element definitions\n'
    f'   - optional `state`: object\n'
    f"   **NEVER emit incremental patches** that assume a previous message's "
    f'spec still exists. Always output the full spec.\n'
    f'5. Each element MUST contain:\n'
    f'   - `type`: component name\n'
    f'   - `props`: object\n'
    f'   - optional `children`: string[]\n'
    f'   - optional `on`: event-action bindings\n'
    f'6. Use RFC 6902 operations: `add`, `replace`, `remove`, `move`, `copy`, '
    f'`test`.\n'
    f'7. For interactive controls (button/form), bind events through `on` '
    f'with action names and params.\n'
    f'8. Prefer structured components over plain text dumps:\n'
    f'   - key/value rows: `KV` or `PriceRow`\n'
    f'   - grouped blocks: `Section`\n'
    f'   - status and tags: `Badge`\n'
    f'   - action area: `ButtonGroup`\n'
    f'9. For order/invoice scenarios, strongly prefer this composition:\n'
    f'   - root `Card` with `variant: "order"`\n'
    f'   - header row with order id + status `Badge`\n'
    f'   - `Section` blocks for shipping, items, and payment\n'
    f'   - each line item uses `OrderItem`\n'
    f'   - totals use `PriceRow`; payable/total amount should set '
    f'`emphasis=true`\n'
    f'   - action buttons are grouped in `ButtonGroup`\n'
    f'10. Available component types include:\n'
    f'   `Card`, `Column`, `Row`, `Text`, `Divider`, `Image`, `Button`, '
    f'`ButtonGroup`,\n'
    f'   `Section`, `KV`, `PriceRow`, `Badge`, `OrderItem`,\n'
    f'   `Input`/`TextField`, `Checkbox`/`CheckBox`, `DateTimeInput`, '
    f'`MultipleChoice`.\n'
    f'11. If you cannot produce valid SpecStream patches, return plain text '
    f'only and DO NOT emit a spec fence.\n'
    f'12. For form scenarios:\n'
    f'   - Define initial form values in `state`: `"state": {{ "form": {{ '
    f'"name": "", "phone": "" }} }}`\n'
    f'   - Bind input values with `{{ "$bindState": "/form/fieldName" }}` on '
    f'the `value` prop\n'
    f'   - **Selection/toggle/filter buttons MUST use the built-in `setState` '
    f'action** to update local state, e.g.:\n'
    f'     `"on": {{ "press": {{ "action": "setState", "params": {{ '
    f'"statePath": "/form/date", "value": "today" }} }} }}`\n'
    f'   - Only the final submit/search/confirm button should use a custom '
    f'action name, e.g.:\n'
    f'     `"on": {{ "press": {{ "action": "searchTrains" }} }}`\n'
    f"   - User's form input values are automatically collected and sent with "
    f'the submit action\n'
    f'13. Built-in actions (setState, pushState, removeState, validateForm) '
    f'are handled client-side.\n'
    f'    Do NOT use these as business action names.\n'
    f'14. Conditional / dynamic prop values — use the `$cond`/`$then`/`$else` '
    f'expression:\n'
    f'   `{{ "$cond": {{ "$state": "/form/trainType", "eq": "G" }}, "$then": '
    f'"primary", "$else": "secondary" }}`\n'
    f'   Condition operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`; add '
    f'`"not": true` to negate.\n'
    f'   Read state: `{{ "$state": "/path" }}` in conditions, `{{ '
    f'"$bindState": "/path" }}` for two-way binding on Input value.\n'
    f'   **NEVER use `$if` or `$eq` as expression keys** — they are NOT '
    f'supported.\n'
    f'   Full example for a toggle-style filter button:\n'
    f'   ```\n'
    f'   {{ "type": "Button", "props": {{ "label": "高铁", "variant": {{ '
    f'"$cond": {{ "$state": "/form/trainType", "eq": "G" }}, "$then": '
    f'"primary", "$else": "secondary" }} }}, "on": {{ "press": {{ "action": '
    f'"setState", "params": {{ "statePath": "/form/trainType", "value": "G" '
    f'}} }} }} }}\n'
    f'   ```\n'
    f'15. Component prop conventions — all form components bind via the '
    f'`value` prop:\n'
    f'   - `Input`/`TextField`: `{{ "value": {{ "$bindState": "/form/field" '
    f'}} }}`\n'
    f'   - `Checkbox`/`CheckBox`: `{{ "value": {{ "$bindState": "/form/flag" '
    f'}} }}` (boolean)\n'
    f'   - `DateTimeInput`: `{{ "value": {{ "$bindState": "/form/date" }} '
    f'}}`\n'
    f'   - `MultipleChoice`: single-select dropdown. Use `value` prop (NOT '
    f'`selectedValues` or `selectedValue`).\n'
    f'     State must be a string, NOT an array. Example:\n'
    f'     `{{ "type": "MultipleChoice", "props": {{ "label": "类型", '
    f'"options": [...], "value": {{ "$bindState": "/form/type" }} }} }}`\n'
)
