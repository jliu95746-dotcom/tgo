"""Derive one execution identity for an event within its project and flow."""

import json
from uuid import NAMESPACE_URL, uuid5


def event_execution_id(
    project_id: str, workflow_id: str, event_id: str
) -> str:
    scope = json.dumps(
        [project_id, workflow_id, event_id], separators=(",", ":")
    )
    return str(uuid5(NAMESPACE_URL, f"tgo-workflow-event:v1:{scope}"))
