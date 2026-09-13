"""Validate tool output before it can become customer logistics history."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from pydantic import JsonValue, TypeAdapter

_OBJECT = TypeAdapter(dict[str, JsonValue])
_MAX_TEXT = 262144


@dataclass(frozen=True)
class ParsedTrackingEvent:
    status: str | None
    description: str
    location: str | None
    event_time: datetime


@dataclass(frozen=True)
class ParsedTrackingResult:
    status: str
    carrier_code: str | None
    carrier_name: str | None
    summary: str | None
    events: tuple[ParsedTrackingEvent, ...]


def _text(data: dict[str, JsonValue], keys: Iterable[str], limit: int) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            if len(value) > limit:
                raise ValueError("Tracking field exceeds limit")
            return value.strip()
    return None


def _object(value: object) -> dict[str, JsonValue]:
    if isinstance(value, str):
        if len(value) > _MAX_TEXT:
            raise ValueError("Tracking response exceeds limit")
        value = json.loads(value)
    return _OBJECT.validate_python(value, strict=True)


def _check_envelope(data: dict[str, JsonValue], expected: str | None) -> None:
    for key in ("success", "ok"):
        if key in data and data[key] is not True:
            raise ValueError("Tracking tool did not succeed")
    if data.get("isError") not in (None, False) or data.get("error") not in (None, False, "", 0):
        raise ValueError("Tracking tool returned an error")
    for key in ("code", "status_code", "status"):
        value = data.get(key)
        if isinstance(value, (int, str)) and str(value).isdigit() and int(value) not in (0, 200):
            raise ValueError("Tracking provider returned a failure code")
    if _text(data, ("status",), 64) in ("error", "failed", "failure", "not_found"):
        raise ValueError("Tracking provider returned a failure status")
    for key in ("tracking_no", "tracking_number", "waybill_no", "bill_no", "nu"):
        if expected and key in data:
            value = data[key]
            if not isinstance(value, str) or re.sub(r"\s+", "", value).upper() != expected:
                raise ValueError("Tracking response belongs to another shipment")


def _unwrap(raw: object, expected: str | None) -> dict[str, JsonValue]:
    data = _object(raw)
    for _ in range(10):
        _check_envelope(data, expected)
        wrapped = next((key for key in ("output_data", "result", "data", "logistics") if key in data), None)
        if wrapped is not None:
            data = _object(data[wrapped])
            continue
        content = data.get("content")
        if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
            data = _object(content)
            continue
        if isinstance(content, list):
            if len(content) > 16:
                raise ValueError("Too many tracking tool content blocks")
            objects = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text.lstrip().startswith("{"):
                        objects.append(_object(text))
            if len(objects) != 1:
                raise ValueError("Tracking tool must return one structured result")
            data = objects[0]
            continue
        return data
    raise ValueError("Tracking response nesting exceeds limit")


def normalize_tracking_status(value: str | None) -> str | None:
    """None means unrecognized; 'unknown' is an explicit non-terminal report."""
    if not value:
        return None
    value = value.strip().lower()
    if re.search(r"异常|失败|退回|拒收|\b(exception|failed|returned)\b", value):
        return "exception"
    if re.search(r"(?:待|等待|未|尚未)揽收|\bpending\b|awaiting collection", value):
        return "pending"
    if re.search(
        r"未签收|未妥投|未送达|待签收|\b(undelivered|unsigned)\b|not(?: \w+){0,2} (?:delivered|signed)", value
    ):
        return "unknown"
    if re.search(r"已签收|签收|已妥投|已送达|\b(delivered|signed)\b", value):
        return "delivered"
    if re.search(r"运输中|在途|派送|\b(in_transit|transit|out_for_delivery)\b", value):
        return "in_transit"
    if re.search(r"揽收|\b(active|collected)\b", value):
        return "active"
    return None


def _event_time(value: JsonValue) -> datetime | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            seconds = value / 1000 if abs(value) >= 100000000000 else value
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if not 2000 <= parsed.year <= 2100:
            return None
        # Preserve the existing adapter convention for offset-less provider dates.
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def parse_tracking_result(raw: object, *, expected_tracking_no: str | None = None) -> ParsedTrackingResult:
    """Only structured, successful, source-matching data can update an archive."""
    expected = re.sub(r"\s+", "", expected_tracking_no).upper() if expected_tracking_no else None
    data = _unwrap(raw, expected)
    status = normalize_tracking_status(_text(data, ("status", "state", "delivery_status"), 64))
    summary = _text(data, ("summary", "message", "latest", "description", "content"), 4000)
    carrier_code = _text(data, ("carrier_code", "company_code"), 64)
    carrier_name = _text(data, ("carrier_name", "company", "carrier"), 128)
    raw_events = next((data[key] for key in ("traces", "events", "tracking", "route") if key in data), [])
    if not isinstance(raw_events, list) or len(raw_events) > 500:
        raise ValueError("Tracking events must be a bounded list")
    events: list[ParsedTrackingEvent] = []
    seen: set[tuple[datetime, str]] = set()
    for item in raw_events:
        if not isinstance(item, dict):
            raise ValueError("Tracking event must be an object")
        description = _text(item, ("description", "context", "message", "status_text"), 4000)
        event_time = _event_time(
            next((item[key] for key in ("time", "event_time", "datetime", "ftime") if key in item), None)
        )
        if not description or event_time is None or (event_time, description) in seen:
            continue
        seen.add((event_time, description))
        events.append(
            ParsedTrackingEvent(
                status=_text(item, ("status", "state"), 64),
                description=description,
                location=_text(item, ("location", "city", "area"), 255),
                event_time=event_time,
            )
        )
    events.sort(key=lambda event: event.event_time, reverse=True)
    if events:
        summary = summary or events[0].description
        if status is None:
            status = normalize_tracking_status(f"{events[0].status or ''} {events[0].description}")
    if status is None and not events:
        raise ValueError("Tool response has no usable tracking facts")
    return ParsedTrackingResult(status or "unknown", carrier_code, carrier_name, summary, tuple(events))
