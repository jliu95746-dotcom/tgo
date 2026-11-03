"""Saved merchant mappings are validated by the owner, not only the browser."""

import pytest
from pydantic import ValidationError

from app.schemas.business_connections import BusinessHTTPConfiguration


def test_saved_configuration_has_backward_compatible_request_defaults():
    saved = BusinessHTTPConfiguration(base_url="https://merchant.example.test")
    assert saved.merchant_id is None
    assert saved.request_order_no_field == "order_no"
    assert saved.order_status_map == {} and saved.not_found_values == []


@pytest.mark.parametrize(
    "changes",
    [
        {"request_customer_id_field": "order_no"},
        {"request_operation_field": "tenant_id"},
        {"order_status_map": {"sent": "private\ntext"}},
        {"not_found_values": ["0"], "success_field": "code"},
        {"default_timezone": "Invalid/Timezone"},
    ],
)
def test_owner_rejects_colliding_or_invalid_mapping(changes):
    with pytest.raises(ValidationError):
        BusinessHTTPConfiguration(base_url="https://merchant.example.test", **changes)
