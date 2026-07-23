"""Focused behavior tests for the customer logistics archive."""

from app.services.customer_logistics_service import (
    detect_tracking_numbers,
    mask_tracking_no,
    normalize_tracking_no,
    parse_tracking_result,
)


def test_detect_tracking_numbers_is_conservative_and_deduplicated() -> None:
    text = "顺丰单号 SF1234567890，另一个是 77312345678901，再说一次 SF1234567890。"

    assert detect_tracking_numbers(text) == (
        "SF1234567890",
        "77312345678901",
    )


def test_tracking_number_normalization_and_masking() -> None:
    assert normalize_tracking_no(" sf1234567890 ") == "SF1234567890"
    assert mask_tracking_no("SF1234567890") == "SF12****7890"


def test_parse_tracking_result_normalizes_common_provider_shape() -> None:
    result = parse_tracking_result(
        {
            "output_data": {
                "data": {
                    "company": "顺丰速运",
                    "status": "运输中",
                    "traces": [
                        {
                            "time": "2026-07-23T10:30:00+00:00",
                            "context": "快件到达上海转运中心",
                            "location": "上海",
                        }
                    ],
                }
            }
        }
    )

    assert result.status == "in_transit"
    assert result.carrier_name == "顺丰速运"
    assert result.summary == "快件到达上海转运中心"
    assert result.events[0].location == "上海"


def test_parse_tracking_result_decodes_json_content_from_store_tool() -> None:
    result = parse_tracking_result(
        {
            "output_data": {
                "content": (
                    '{"status":"已签收","company":"圆通速递",'
                    '"message":"本人已签收"}'
                )
            }
        }
    )

    assert result.status == "delivered"
    assert result.carrier_name == "圆通速递"
    assert result.summary == "本人已签收"
