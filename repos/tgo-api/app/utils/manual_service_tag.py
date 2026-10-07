"""Shared constants for the manual service (human handoff) tag.

Keep this module dependency-light so it can be imported from both internal and
public API modules without pulling in ORM models.
"""

from __future__ import annotations

import base64
from uuid import UUID


MANUAL_SERVICE_TAG_NAME: str = "Manual Service"
MANUAL_SERVICE_TAG_NAME_ZH: str = "转人工"

# TagCategory.VISITOR.value == "visitor"
MANUAL_SERVICE_TAG_ID: str = base64.b64encode(f"{MANUAL_SERVICE_TAG_NAME}@visitor".encode()).decode()


def manual_service_tag_id(project_id: UUID) -> str:
    """Give new tenants a distinct tag ID while retaining legacy IDs."""
    value = f"{MANUAL_SERVICE_TAG_NAME}@visitor@{project_id}"
    return base64.b64encode(value.encode()).decode()


def manual_service_tag_ids(project_id: UUID) -> tuple[str, str]:
    return MANUAL_SERVICE_TAG_ID, manual_service_tag_id(project_id)

