"""Customer logistics archive and live-query orchestration."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
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
from app.services.logistics_result_parser import (
    ParsedTrackingResult as ParsedTrackingResult,
    normalize_tracking_status,
    parse_tracking_result as parse_tracking_result,
)
from app.utils.crypto import decrypt_str, encrypt_str


_TRACKING_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{8,32}(?![A-Za-z0-9])")
_KNOWN_PREFIXES = ("SF", "YT", "JD", "ZTO", "STO", "YTO", "EMS", "JT", "DB")


def normalize_tracking_no(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z0-9]{8,32}", normalized):
        raise ValueError("物流单号只能包含 8-32 位字母或数字")
    if sum(character.isdigit() for character in normalized) < 6:
        raise ValueError("物流单号至少需要包含 6 位数字")
    return normalized


def _validated_tracking_no(value: str) -> str:
    try:
        return normalize_tracking_no(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


class CustomerLogisticsService:
    def __init__(self, db: Session):
        self.db = db

    def get_settings(self, project_id: UUID) -> LogisticsSettings:
        settings_row = (
            self.db.query(LogisticsSettings).filter(LogisticsSettings.project_id == project_id).one_or_none()
        )
        if settings_row is None:
            settings_row = LogisticsSettings(project_id=project_id)
            self.db.add(settings_row)
            self.db.commit()
            self.db.refresh(settings_row)
        return settings_row

    def update_settings(self, project_id: UUID, update: LogisticsSettingsUpdate) -> LogisticsSettings:
        settings_row = self.get_settings(project_id)
        for field, value in update.model_dump().items():
            setattr(settings_row, field, value)
        self.db.commit()
        self.db.refresh(settings_row)
        return settings_row

    def list_shipments(self, project_id: UUID, visitor_id: UUID) -> tuple[CustomerShipment, ...]:
        settings_row = self.get_settings(project_id)
        archive_cutoff = datetime.now(timezone.utc) - timedelta(days=settings_row.archive_after_days)
        return tuple(
            self.db.query(CustomerShipment)
            .filter(
                CustomerShipment.project_id == project_id,
                CustomerShipment.visitor_id == visitor_id,
                CustomerShipment.archived_at.is_(None),
                (CustomerShipment.delivered_at.is_(None) | (CustomerShipment.delivered_at >= archive_cutoff)),
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
        normalized = _validated_tracking_no(tracking_no)
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
                if source in {"staff_message", "manual", "order_sync"} or not settings_row.verify_before_binding
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

    def get_shipment(self, project_id: UUID, shipment_id: UUID, *, for_update: bool = False) -> CustomerShipment:
        query = self.db.query(CustomerShipment).filter(
            CustomerShipment.project_id == project_id,
            CustomerShipment.id == shipment_id,
        )
        if for_update:
            query = query.populate_existing().with_for_update()
        shipment = query.one_or_none()
        if shipment is None:
            raise HTTPException(status_code=404, detail="物流档案不存在")
        return shipment

    def list_events(self, project_id: UUID, shipment_id: UUID) -> tuple[ShipmentTrackingEvent, ...]:
        self.get_shipment(project_id, shipment_id)
        return tuple(
            self.db.query(ShipmentTrackingEvent)
            .filter(ShipmentTrackingEvent.shipment_id == shipment_id)
            .order_by(ShipmentTrackingEvent.event_time.desc())
            .all()
        )

    async def execute_live_query(
        self,
        project_id: UUID,
        tracking_no: str,
        visitor_id: UUID | None = None,
        *,
        query_tool_id: UUID | None = None,
        carrier_code: str | None = None,
        phone: str | None = None,
    ) -> ParsedTrackingResult:
        selected_tool_id = query_tool_id
        if selected_tool_id is None:
            selected_tool_id = self.get_settings(project_id).query_tool_id
        if selected_tool_id is None:
            raise HTTPException(
                status_code=409,
                detail="尚未在物流设置中选择实时快递查询工具",
            )
        normalized = _validated_tracking_no(tracking_no)
        query_input = {"tracking_no": normalized}
        if carrier_code:
            query_input["carrier_code"] = carrier_code
        if phone:
            query_input["phone"] = phone
        result = await ai_client.execute_tool(
            project_id=str(project_id),
            tool_id=str(selected_tool_id),
            input_data=query_input,
            visitor_id=str(visitor_id) if visitor_id else None,
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise HTTPException(status_code=502, detail="快递查询工具执行失败")
        try:
            return parse_tracking_result(result, expected_tracking_no=normalized)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="快递查询未返回有效轨迹，请稍后重试。") from exc

    async def query_shipment(
        self, project_id: UUID, shipment_id: UUID
    ) -> tuple[CustomerShipment, tuple[ShipmentTrackingEvent, ...]]:
        started_at = datetime.now(timezone.utc)
        shipment = self.get_shipment(project_id, shipment_id)
        if shipment.verification_state == "conflict":
            raise HTTPException(status_code=409, detail="物流单号归属有冲突，请先人工核对。")
        tracking_no = decrypt_str(shipment.tracking_no_ciphertext)
        if tracking_no is None:
            raise HTTPException(status_code=500, detail="物流单号无法解密")
        parsed = await self.execute_live_query(project_id, tracking_no, shipment.visitor_id)
        # A slower request cannot overwrite a newer completed query or erase a
        # conflict detected while the provider request was in flight.
        shipment = self.get_shipment(project_id, shipment_id, for_update=True)
        if shipment.verification_state == "conflict":
            raise HTTPException(status_code=409, detail="物流单号归属有冲突，请先人工核对。")
        checked_at = shipment.last_checked_at
        if checked_at is not None:
            checked_at = checked_at if checked_at.tzinfo else checked_at.replace(tzinfo=timezone.utc)
            if checked_at >= started_at:
                self.db.commit()
                return shipment, self.list_events(project_id, shipment.id)
        shipment.status = parsed.status
        shipment.carrier_code = parsed.carrier_code or shipment.carrier_code
        shipment.carrier_name = parsed.carrier_name or shipment.carrier_name
        shipment.latest_summary = parsed.summary
        shipment.verification_state = "verified"
        # Use query start time as the ordering fence: the latest started query
        # wins regardless of which HTTP response completes first.
        shipment.last_checked_at = started_at
        if parsed.status == "delivered":
            carrier_delivered_at = next(
                (
                    event.event_time
                    for event in parsed.events
                    if normalize_tracking_status(f"{event.status or ''} {event.description}") == "delivered"
                ),
                None,
            )
            if carrier_delivered_at is not None:
                shipment.delivered_at = carrier_delivered_at
        else:
            shipment.delivered_at = None
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
