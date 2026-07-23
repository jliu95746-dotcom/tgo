"""Customer logistics archive and live-query orchestration."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.customer_logistics import (
    CustomerShipment,
    LogisticsSettings,
    ShipmentTrackingEvent,
)
from app.schemas.customer_logistics import LogisticsSettingsUpdate
from app.services.ai_client import ai_client
from app.utils.crypto import decrypt_str, encrypt_str


_TRACKING_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{8,32}(?![A-Za-z0-9])")
_KNOWN_PREFIXES = ("SF", "YT", "JD", "ZTO", "STO", "YTO", "EMS", "JT", "DB")
_STATUS_MAP = {
    "delivered": "delivered",
    "signed": "delivered",
    "已签收": "delivered",
    "签收": "delivered",
    "exception": "exception",
    "异常": "exception",
    "in_transit": "in_transit",
    "transit": "in_transit",
    "运输中": "in_transit",
    "派送": "in_transit",
    "pending": "pending",
    "揽收": "active",
    "collected": "active",
}


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


def normalize_tracking_no(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z0-9]{8,32}", normalized):
        raise ValueError("物流单号只能包含 8-32 位字母或数字")
    if sum(character.isdigit() for character in normalized) < 6:
        raise ValueError("物流单号至少需要包含 6 位数字")
    return normalized


def mask_tracking_no(value: str) -> str:
    normalized = normalize_tracking_no(value)
    if len(normalized) <= 10:
        return f"{normalized[:2]}****{normalized[-2:]}"
    return f"{normalized[:4]}****{normalized[-4:]}"


def tracking_hash(value: str) -> str:
    return hashlib.sha256(normalize_tracking_no(value).encode("utf-8")).hexdigest()


def detect_tracking_numbers(text: str) -> tuple[str, ...]:
    """Conservatively extract likely tracking numbers from a chat message."""

    results: list[str] = []
    for candidate in _TRACKING_CANDIDATE.findall(text.upper()):
        digit_count = sum(character.isdigit() for character in candidate)
        looks_known = candidate.startswith(_KNOWN_PREFIXES)
        looks_numeric = candidate.isdigit() and 10 <= len(candidate) <= 20
        if digit_count >= 8 and (looks_known or looks_numeric or digit_count >= 10):
            try:
                normalized = normalize_tracking_no(candidate)
            except ValueError:
                continue
            if normalized not in results:
                results.append(normalized)
    return tuple(results)


def _first_text(data: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_status(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = value.strip().lower()
    for key, status in _STATUS_MAP.items():
        if key in lowered:
            return status
    return "unknown"


def parse_tracking_result(raw: Any) -> ParsedTrackingResult:
    """Normalize common express-provider response shapes."""

    if isinstance(raw, str):
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            data = {"summary": raw}
    else:
        data = raw
    while isinstance(data, dict):
        nested = next(
            (
                data[key]
                for key in ("output_data", "result", "data", "logistics")
                if isinstance(data.get(key), dict)
            ),
            None,
        )
        if nested is None:
            break
        data = nested
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        content = data["content"].strip()
        try:
            decoded_content = json.loads(content)
        except json.JSONDecodeError:
            decoded_content = None
        if isinstance(decoded_content, (dict, list)):
            data = decoded_content
    if not isinstance(data, dict):
        data = {"summary": str(data)}

    status_text = _first_text(data, ("status", "state", "delivery_status"))
    summary = _first_text(
        data, ("summary", "message", "latest", "description", "content")
    )
    carrier_code = _first_text(data, ("carrier_code", "company_code", "code"))
    carrier_name = _first_text(data, ("carrier_name", "company", "carrier"))
    raw_events = next(
        (
            data[key]
            for key in ("traces", "events", "tracking", "route")
            if isinstance(data.get(key), list)
        ),
        [],
    )
    events: list[ParsedTrackingEvent] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        description = _first_text(
            item, ("description", "context", "message", "status_text")
        )
        if not description:
            continue
        event_status = _first_text(item, ("status", "state"))
        events.append(
            ParsedTrackingEvent(
                status=event_status,
                description=description,
                location=_first_text(item, ("location", "city", "area")),
                event_time=_parse_datetime(
                    next(
                        (
                            item[key]
                            for key in ("time", "event_time", "datetime", "ftime")
                            if item.get(key) is not None
                        ),
                        None,
                    )
                ),
            )
        )
    events.sort(key=lambda event: event.event_time, reverse=True)
    if summary is None and events:
        summary = events[0].description
    derived_status = _normalize_status(status_text)
    if derived_status == "unknown" and events:
        derived_status = _normalize_status(
            f"{events[0].status or ''} {events[0].description}"
        )
    return ParsedTrackingResult(
        status=derived_status,
        carrier_code=carrier_code,
        carrier_name=carrier_name,
        summary=summary,
        events=tuple(events),
    )


class CustomerLogisticsService:
    def __init__(self, db: Session):
        self.db = db

    def get_settings(self, project_id: UUID) -> LogisticsSettings:
        settings_row = (
            self.db.query(LogisticsSettings)
            .filter(LogisticsSettings.project_id == project_id)
            .one_or_none()
        )
        if settings_row is None:
            settings_row = LogisticsSettings(project_id=project_id)
            self.db.add(settings_row)
            self.db.commit()
            self.db.refresh(settings_row)
        return settings_row

    def update_settings(
        self, project_id: UUID, update: LogisticsSettingsUpdate
    ) -> LogisticsSettings:
        settings_row = self.get_settings(project_id)
        for field, value in update.model_dump().items():
            setattr(settings_row, field, value)
        self.db.commit()
        self.db.refresh(settings_row)
        return settings_row

    def list_shipments(
        self, project_id: UUID, visitor_id: UUID
    ) -> tuple[CustomerShipment, ...]:
        settings_row = self.get_settings(project_id)
        archive_cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings_row.archive_after_days
        )
        return tuple(
            self.db.query(CustomerShipment)
            .filter(
                CustomerShipment.project_id == project_id,
                CustomerShipment.visitor_id == visitor_id,
                CustomerShipment.archived_at.is_(None),
                (
                    CustomerShipment.delivered_at.is_(None)
                    | (CustomerShipment.delivered_at >= archive_cutoff)
                ),
            )
            .order_by(CustomerShipment.updated_at.desc())
            .all()
        )

    def create_shipment(
        self,
        *,
        project_id: UUID,
        visitor_id: UUID,
        tracking_no: str,
        source: str,
        source_message_id: str | None = None,
        carrier_code: str | None = None,
        carrier_name: str | None = None,
    ) -> CustomerShipment:
        normalized = normalize_tracking_no(tracking_no)
        digest = tracking_hash(normalized)
        existing = (
            self.db.query(CustomerShipment)
            .filter(
                CustomerShipment.project_id == project_id,
                CustomerShipment.tracking_no_hash == digest,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.visitor_id != visitor_id:
                settings_row = self.get_settings(project_id)
                if settings_row.conflict_policy == "manual_review":
                    existing.verification_state = "conflict"
                    self.db.commit()
                raise HTTPException(
                    status_code=409,
                    detail="该物流单号已归档到其他顾客，请人工核对",
                )
            if source_message_id:
                existing.last_source_message_id = source_message_id
            if existing.archived_at is not None:
                existing.archived_at = None
            self.db.commit()
            self.db.refresh(existing)
            return existing

        settings_row = self.get_settings(project_id)
        shipment = CustomerShipment(
            project_id=project_id,
            visitor_id=visitor_id,
            tracking_no_ciphertext=encrypt_str(normalized),
            tracking_no_hash=digest,
            tracking_no_masked=mask_tracking_no(normalized),
            carrier_code=carrier_code,
            carrier_name=carrier_name,
            status="unknown",
            source=source,
            verification_state=(
                "verified"
                if source in {"staff_message", "manual", "order_sync"}
                or not settings_row.verify_before_binding
                else "pending"
            ),
            last_source_message_id=source_message_id,
        )
        self.db.add(shipment)
        self.db.commit()
        self.db.refresh(shipment)
        return shipment

    def capture_message(
        self,
        *,
        project_id: UUID,
        visitor_id: UUID,
        message_text: str,
        source: str,
        source_message_id: str,
    ) -> tuple[CustomerShipment, ...]:
        detected_numbers = detect_tracking_numbers(message_text)
        if not detected_numbers:
            return ()
        settings_row = self.get_settings(project_id)
        if not settings_row.enabled:
            return ()
        if source == "visitor_message" and not settings_row.auto_capture_visitor_messages:
            return ()
        if source == "staff_message" and not settings_row.auto_capture_staff_messages:
            return ()

        captured: list[CustomerShipment] = []
        for tracking_no in detected_numbers:
            try:
                captured.append(
                    self.create_shipment(
                        project_id=project_id,
                        visitor_id=visitor_id,
                        tracking_no=tracking_no,
                        source=source,
                        source_message_id=source_message_id,
                    )
                )
            except HTTPException as error:
                if error.status_code != 409:
                    raise
        return tuple(captured)

    def get_shipment(
        self, project_id: UUID, shipment_id: UUID
    ) -> CustomerShipment:
        shipment = (
            self.db.query(CustomerShipment)
            .filter(
                CustomerShipment.project_id == project_id,
                CustomerShipment.id == shipment_id,
            )
            .one_or_none()
        )
        if shipment is None:
            raise HTTPException(status_code=404, detail="物流档案不存在")
        return shipment

    def list_events(
        self, project_id: UUID, shipment_id: UUID
    ) -> tuple[ShipmentTrackingEvent, ...]:
        self.get_shipment(project_id, shipment_id)
        return tuple(
            self.db.query(ShipmentTrackingEvent)
            .filter(ShipmentTrackingEvent.shipment_id == shipment_id)
            .order_by(ShipmentTrackingEvent.event_time.desc())
            .all()
        )

    async def execute_live_query(
        self, project_id: UUID, tracking_no: str, visitor_id: UUID | None = None
    ) -> ParsedTrackingResult:
        settings_row = self.get_settings(project_id)
        if settings_row.query_tool_id is None:
            raise HTTPException(
                status_code=409,
                detail="尚未在物流设置中选择实时快递查询工具",
            )
        result = await ai_client.execute_tool(
            project_id=str(project_id),
            tool_id=str(settings_row.query_tool_id),
            input_data={"tracking_no": normalize_tracking_no(tracking_no)},
            visitor_id=str(visitor_id) if visitor_id else None,
        )
        if not isinstance(result, dict) or result.get("success") is False:
            raise HTTPException(status_code=502, detail="快递查询工具执行失败")
        return parse_tracking_result(result.get("output_data", result))

    async def query_shipment(
        self, project_id: UUID, shipment_id: UUID
    ) -> tuple[CustomerShipment, tuple[ShipmentTrackingEvent, ...]]:
        shipment = self.get_shipment(project_id, shipment_id)
        tracking_no = decrypt_str(shipment.tracking_no_ciphertext)
        if tracking_no is None:
            raise HTTPException(status_code=500, detail="物流单号无法解密")
        parsed = await self.execute_live_query(
            project_id, tracking_no, shipment.visitor_id
        )
        shipment.status = parsed.status
        shipment.carrier_code = parsed.carrier_code or shipment.carrier_code
        shipment.carrier_name = parsed.carrier_name or shipment.carrier_name
        shipment.latest_summary = parsed.summary
        shipment.verification_state = "verified"
        shipment.last_checked_at = datetime.now(timezone.utc)
        if parsed.status == "delivered" and shipment.delivered_at is None:
            shipment.delivered_at = shipment.last_checked_at
        for event in parsed.events:
            exists = (
                self.db.query(ShipmentTrackingEvent.id)
                .filter(
                    ShipmentTrackingEvent.shipment_id == shipment.id,
                    ShipmentTrackingEvent.event_time == event.event_time,
                    ShipmentTrackingEvent.description == event.description,
                )
                .first()
            )
            if exists is None:
                self.db.add(
                    ShipmentTrackingEvent(
                        shipment_id=shipment.id,
                        status=event.status,
                        description=event.description,
                        location=event.location,
                        event_time=event.event_time,
                    )
                )
        self.db.commit()
        self.db.refresh(shipment)
        return shipment, self.list_events(project_id, shipment.id)
