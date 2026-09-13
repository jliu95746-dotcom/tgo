"""Resolved humanization settings shared by staff UI and customer replies."""
from typing import Literal

from pydantic import BaseModel


class EmployeeStyle(BaseModel):
    skill_name: str | None = None
    enabled: bool = False
    source: Literal["employee", "conversation"] = "employee"
    agent_id: str | None = None
    agent_name: str | None = None
