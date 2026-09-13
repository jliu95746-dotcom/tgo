"""Internal tool editing rejects invalid nulls but supports clearing metadata."""

import pytest
from pydantic import ValidationError

from app.schemas.tool import ToolUpdate


@pytest.mark.parametrize("field", ["name", "tool_type"])
def test_explicit_null_required_field_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        ToolUpdate.model_validate({field: None})


def test_optional_clears_and_omitted_fields_remain_distinct() -> None:
    assert ToolUpdate().model_dump(exclude_unset=True) == {}
    assert ToolUpdate(description=None, title=None).model_dump(
        exclude_unset=True,
    ) == {"description": None, "title": None}
