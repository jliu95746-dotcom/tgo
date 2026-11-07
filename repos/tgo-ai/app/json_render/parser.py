"""Parse mixed LLM output (text + ```spec JSONL blocks) into json-render patches."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.json_render.schema_manager import (
    JSON_RENDER_SPEC_FENCE_CLOSE,
    JSON_RENDER_SPEC_FENCE_OPEN,
)
from app.json_render.validator import JsonRenderPatchValidator


@dataclass
class JsonRenderParseResult:
    """Result of parsing LLM output for json-render content."""

    text_content: str = ""
    patches: List[Dict[str, Any]] = field(default_factory=list)
    has_json_render: bool = False
    is_valid: bool = True
    validation_error: Optional[str] = None


class JsonRenderParser:
    """Extract and validate json-render patch lines from model output."""

    def __init__(
        self, validator: Optional[JsonRenderPatchValidator] = None
    ) -> None:
        self._validator = validator or JsonRenderPatchValidator()

    def parse(self, content: str) -> JsonRenderParseResult:
        """Parse mixed text + fenced spec stream output."""
        in_spec_fence = False
        text_lines: List[str] = []
        patches: List[Dict[str, Any]] = []

        for raw_line in content.splitlines():
            line = raw_line.rstrip("\r")
            trimmed = line.strip()

            if not in_spec_fence and trimmed.startswith(
                JSON_RENDER_SPEC_FENCE_OPEN
            ):
                in_spec_fence = True
                continue
            if in_spec_fence and trimmed == JSON_RENDER_SPEC_FENCE_CLOSE:
                in_spec_fence = False
                continue
            parsed_patch = self._parse_patch_line(trimmed)
            if parsed_patch is not None:
                patches.append(parsed_patch)
                continue
            if not in_spec_fence:
                text_lines.append(line)
        text_content = "\n".join(text_lines).strip()
        if not patches:
            return JsonRenderParseResult(text_content=text_content)
        is_valid, error = self._validator.validate(patches)
        return JsonRenderParseResult(
            text_content=text_content,
            patches=patches,
            has_json_render=True,
            is_valid=is_valid,
            validation_error=error,
        )

    @staticmethod
    def _parse_patch_line(line: str) -> Optional[Dict[str, Any]]:
        if not line or not line.startswith("{"):
            return None
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        op = parsed.get("op")
        path = parsed.get("path")
        if (
            not isinstance(op, str)
            or not isinstance(path, str)
            or not path.startswith("/")
        ):
            return None
        return parsed
