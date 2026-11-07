"""Helpers for rolling platform AI settings toward single-agent routing."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID


def choose_platform_agent_id(agent_ids: Sequence[UUID] | None) -> UUID | None:
    (
        'Return the deterministic platform-level agent override, if '
        'configured.'
    )

    if not agent_ids:
        return None

    return agent_ids[0]
