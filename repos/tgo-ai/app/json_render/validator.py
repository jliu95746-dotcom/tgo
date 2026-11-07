"""Validation for json-render SpecStream patch lines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _MODULE_DIR / "schema" / "spec_stream_line.json"
_ALLOWED_OPS = {"add", "remove", "replace", "move", "copy", "test"}

try:
    import jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False
    logger.warning(
        "jsonschema package not installed; json-render schema validation disabled"
    )


class JsonRenderPatchValidator:
    """Validates json-render SpecStream patch lines."""

    def __init__(self, schema_path: Optional[Path] = None) -> None:
        self._schema: Optional[Dict[str, Any]] = None
        path = schema_path or _SCHEMA_PATH
        if path.exists():
            self._schema = json.loads(path.read_text(encoding="utf-8"))

    def validate(
        self, patches: List[Dict[str, Any]]
    ) -> Tuple[bool, Optional[str]]:
        """Validate patch lines with lightweight and optional JSON Schema checks."""
        errors: List[str] = []

        for idx, patch in enumerate(patches):
            op = patch.get("op")
            path = patch.get("path")
            if not isinstance(op, str) or op not in _ALLOWED_OPS:
                errors.append(
                    f"patch[{idx}].op must be one of {sorted(_ALLOWED_OPS)}"
                )
                continue
            if not isinstance(path, str) or not path.startswith("/"):
                errors.append(f"patch[{idx}].path must be a JSON Pointer path")
            if op in {"add", "replace", "test"} and "value" not in patch:
                errors.append(
                    f"patch[{idx}] missing required field 'value' for op={op}"
                )
            if op in {"move", "copy"} and not isinstance(
                patch.get("from"), str
            ):
                errors.append(
                    f"patch[{idx}] missing required field 'from' for op={op}"
                )
            if _HAS_JSONSCHEMA and self._schema is not None:
                try:
                    jsonschema.validate(instance=patch, schema=self._schema)
                except jsonschema.ValidationError as exc:
                    errors.append(f"patch[{idx}]: {exc.message}")
        if errors:
            combined = "; ".join(errors[:4])
            logger.warning("json-render validation failed", errors=combined)
            return False, combined
        return True, None
