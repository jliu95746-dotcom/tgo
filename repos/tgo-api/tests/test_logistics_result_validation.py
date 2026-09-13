"""Reject false logistics successes and preserve source facts."""

from datetime import datetime, timezone

import pytest

from app.services.customer_logistics_service import parse_tracking_result


@pytest.mark.parametrize(
    "raw",
    [
        {},
        None,
        [],
        "查询成功",
        {"success": True, "output_data": {}},
        {"error": "owned failure", "status": "delivered"},
        {"success": False, "output_data": {"status": "delivered"}},
        {"output_data": {"isError": True, "content": [{"type": "text", "text": '{"status":"delivered"}'}]}},
        {"output_data": {"code": 404, "message": "not found"}},
        {"status": "unknown", "message": "查询接口正常"},
        {"traces": [{"context": "运输中", "time": "not-a-time"}]},
    ],
)
def test_empty_or_failed_tool_payload_is_not_tracking_data(raw):
    with pytest.raises(ValueError):
        parse_tracking_result(raw)


@pytest.mark.parametrize("status", ["未签收", "尚未签收", "未妥投", "undelivered", "not delivered", "unsigned"])
def test_negative_delivery_status_is_never_delivered(status):
    result = parse_tracking_result({"status": status})
    assert result.status != "delivered"


@pytest.mark.parametrize("status", ["待揽收", "等待揽收", "尚未揽收", "未揽收"])
def test_waiting_collection_is_pending_not_collected(status):
    assert parse_tracking_result({"status": status}).status == "pending"


def test_invalid_or_missing_time_is_not_replaced_with_now():
    result = parse_tracking_result(
        {
            "status": "in_transit",
            "traces": [
                {"context": "未提供时间"},
                {"context": "时间错误", "time": "invalid"},
                {"context": "有效轨迹", "time": "2026-09-08T08:00:00+08:00"},
            ],
        }
    )
    assert [event.description for event in result.events] == ["有效轨迹"]
    assert result.events[0].event_time == datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_repeated_tracking_events_are_deduplicated_and_milliseconds_are_supported():
    event = {"context": "快件到达转运中心", "time": 1788825600000}
    result = parse_tracking_result({"traces": [event, event]})
    assert len(result.events) == 1
    assert result.events[0].event_time == datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_tracking_number_in_provider_result_must_match_requested_number():
    with pytest.raises(ValueError):
        parse_tracking_result(
            {"tracking_no": "SF9999999999", "status": "delivered"}, expected_tracking_no="SF1234567890"
        )


def test_tracking_identity_in_outer_envelope_is_checked_before_unwrapping():
    with pytest.raises(ValueError):
        parse_tracking_result(
            {"tracking_no": "SF9999999999", "data": {"status": "delivered"}}, expected_tracking_no="SF1234567890"
        )


def test_matching_tool_result_can_be_nested_inside_json_mcp_content():
    result = parse_tracking_result(
        {
            "success": True,
            "output_data": {
                "content": [
                    {
                        "type": "text",
                        "text": '{"data":{"tracking_no":"SF1234567890","status":"delivered","summary":"已送达"}}',
                    }
                ]
            },
        },
        expected_tracking_no="SF1234567890",
    )
    assert result.status == "delivered"
    assert result.summary == "已送达"
