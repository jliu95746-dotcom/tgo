"""Workflow cards must not advertise an unsafe raw HTTP order query as ready."""

import pytest

from app.core.exceptions import ConflictError
from app.services.scenario_templates import get_template, list_templates


def test_order_workflow_template_waits_for_scoped_business_query():
    template = next(item for item in list_templates("workflows") if item.id == "logistics")
    assert template.available is False
    with pytest.raises(ConflictError):
        get_template("workflows", "logistics", 1)


def test_order_agent_template_remains_available():
    assert get_template("agents", "logistics", 1).available is True
