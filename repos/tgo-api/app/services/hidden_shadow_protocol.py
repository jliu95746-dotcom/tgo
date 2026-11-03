"""Pre-register and freeze independent WeCom G4 candidate populations."""

from __future__ import annotations

import hashlib
import itertools
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_protocol import (
    HiddenShadowProtocol, HiddenShadowProtocolCase,
)
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.services.hidden_shadow_pairing import (
    HiddenShadowComparison, independent_shadow_comparisons,
)
from app.schemas.hidden_shadow_protocol import (
    HiddenShadowProtocolCaseItem, HiddenShadowProtocolCasePage,
    HiddenShadowProtocolIntegrityResponse,
)


ALGORITHM = "sha256-seed-wecom-kf-independent-v3"
MAX_POPULATION = 20_000


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _request_fingerprint(
    start: datetime, end: datetime, sample_size: int,
) -> str:
    encoded = json.dumps({
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "sample_size": sample_size,
        "algorithm": ALGORITHM,
    }, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _registration_matches(protocol: HiddenShadowProtocol) -> bool:
    """Check the frozen registration terms against their original records."""
    seed = protocol.random_seed
    if (
        len(seed) != 32
        or any(char not in "0123456789abcdef" for char in seed)
        or protocol.seed_sha256 is None
    ):
        return False
    return (
        protocol.algorithm == ALGORITHM
        and hashlib.sha256(bytes.fromhex(seed)).hexdigest()
        == protocol.seed_sha256
        and protocol.request_fingerprint == _request_fingerprint(
            _utc(protocol.window_start), _utc(protocol.window_end),
            protocol.sample_size,
        )
        and _utc(protocol.created_at) < _utc(protocol.window_start)
        and _utc(protocol.window_start) < _utc(protocol.window_end)
        and (
            protocol.finalized_at is None
            or _utc(protocol.finalized_at) >= _utc(protocol.window_end)
        )
    )


def _manifest_digests(
    comparisons: list[HiddenShadowComparison],
) -> tuple[str, str]:
    def digest_bytes(value: str) -> bytes:
        if (
            len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise ValueError("Malformed SHA-256 digest")
        return bytes.fromhex(value)

    population_digest = hashlib.sha256()
    manifest_digest = hashlib.sha256()
    for item in comparisons:
        population_digest.update(item.candidate_id.bytes)
        manifest_digest.update(item.candidate_id.bytes)
        manifest_digest.update(item.visitor_id.bytes)
        manifest_digest.update(item.staff_delivery_id.bytes)
        manifest_digest.update(item.source_message_id.encode() + b"\0")
        manifest_digest.update(digest_bytes(item.source_content_sha256))
        manifest_digest.update(digest_bytes(item.candidate_sha256))
        manifest_digest.update(digest_bytes(item.staff_reply_sha256))
        manifest_digest.update(
            _utc(item.candidate_completed_at).isoformat().encode() + b"\0"
        )
        manifest_digest.update(
            _utc(item.staff_reply_created_at).isoformat().encode() + b"\0"
        )
    return population_digest.hexdigest(), manifest_digest.hexdigest()


class HiddenShadowProtocolService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, project_id: UUID, protocol_id: UUID) -> HiddenShadowProtocol:
        row = self._db.scalar(select(HiddenShadowProtocol).where(
            HiddenShadowProtocol.id == protocol_id,
            HiddenShadowProtocol.project_id == project_id,
        ))
        if row is None:
            raise TGOAPIException(
                "独立影子验收预登记不存在",
                "HIDDEN_PROTOCOL_NOT_FOUND", status_code=404,
            )
        return row

    def list(
        self, project_id: UUID, *, limit: int,
    ) -> tuple[list[HiddenShadowProtocol], bool]:
        rows = self._db.scalars(
            select(HiddenShadowProtocol)
            .where(HiddenShadowProtocol.project_id == project_id)
            .order_by(
                HiddenShadowProtocol.created_at.desc(),
                HiddenShadowProtocol.id.desc(),
            )
            .limit(limit + 1)
        ).all()
        return list(rows[:limit]), len(rows) > limit

    def list_cases(
        self,
        project_id: UUID,
        protocol_id: UUID,
        *,
        limit: int,
        after_candidate_id: UUID | None = None,
        selected_only: bool = True,
    ) -> HiddenShadowProtocolCasePage:
        protocol = self.get(project_id, protocol_id)
        if protocol.finalized_at is None:
            raise TGOAPIException(
                "独立影子验收候选清单尚未冻结",
                "HIDDEN_PROTOCOL_NOT_READY", status_code=409,
            )
        if after_candidate_id is not None:
            cursor = self._db.scalar(
                select(HiddenShadowProtocolCase.candidate_id).where(
                    HiddenShadowProtocolCase.protocol_id == protocol_id,
                    HiddenShadowProtocolCase.candidate_id
                    == after_candidate_id,
                    *([HiddenShadowProtocolCase.selected_position.is_not(None)]
                      if selected_only else []),
                )
            )
            if cursor is None:
                raise TGOAPIException(
                    "独立影子候选清单游标不存在",
                    "HIDDEN_PROTOCOL_CURSOR_NOT_FOUND", status_code=404,
                )
        query = select(HiddenShadowProtocolCase).where(
            HiddenShadowProtocolCase.protocol_id == protocol_id,
        )
        if selected_only:
            query = query.where(
                HiddenShadowProtocolCase.selected_position.is_not(None)
            )
        if after_candidate_id is not None:
            query = query.where(
                HiddenShadowProtocolCase.candidate_id > after_candidate_id
            )
        rows = self._db.scalars(
            query.order_by(HiddenShadowProtocolCase.candidate_id)
            .limit(limit + 1)
        ).all()
        visible = rows[:limit]
        return HiddenShadowProtocolCasePage(
            protocol_id=protocol_id,
            total_cases=(protocol.sample_count if selected_only
                         else protocol.population_count) or 0,
            items=tuple(HiddenShadowProtocolCaseItem(
                candidate_id=item.candidate_id,
                visitor_id=item.visitor_id,
                selected_position=item.selected_position,
            ) for item in visible),
            next_cursor=(visible[-1].candidate_id
                         if len(rows) > limit else None),
        )

    def verify_integrity(
        self, project_id: UUID, protocol_id: UUID,
    ) -> HiddenShadowProtocolIntegrityResponse:
        protocol = self.get(project_id, protocol_id)
        if protocol.finalized_at is None:
            raise TGOAPIException(
                "独立影子验收候选清单尚未冻结",
                "HIDDEN_PROTOCOL_NOT_READY", status_code=409,
            )
        cases = self._db.scalars(
            select(HiddenShadowProtocolCase)
            .where(HiddenShadowProtocolCase.protocol_id == protocol.id)
            .order_by(HiddenShadowProtocolCase.candidate_id)
        ).all()
        comparisons = [HiddenShadowComparison(
            candidate_id=item.candidate_id,
            visitor_id=item.visitor_id,
            staff_delivery_id=item.staff_delivery_id,
            source_message_id=item.source_message_id,
            source_content_sha256=item.source_content_sha256,
            candidate_sha256=item.candidate_sha256,
            staff_reply_sha256=item.staff_reply_sha256,
            candidate_completed_at=item.candidate_completed_at,
            staff_reply_created_at=item.staff_reply_created_at,
        ) for item in cases]
        population_digest = hashlib.sha256()
        for item in comparisons:
            population_digest.update(item.candidate_id.bytes)
        population_hash = population_digest.hexdigest()
        try:
            _, manifest_hash = _manifest_digests(comparisons)
        except (TypeError, ValueError):
            manifest_hash = None
        seed_valid = (
            len(protocol.random_seed) == 32
            and all(
                char in "0123456789abcdef"
                for char in protocol.random_seed
            )
        )
        metadata_valid = (
            protocol.algorithm == ALGORITHM
            and _registration_matches(protocol)
            and 1 <= protocol.sample_size <= 500
            and protocol.population_count is not None
            and protocol.sample_count is not None
            and protocol.sample_count == min(
                protocol.sample_size, len(cases)
            )
        )
        expected: dict[UUID, int] = {}
        if seed_valid:
            seed = bytes.fromhex(protocol.random_seed)
            ranked = sorted(
                comparisons,
                key=lambda item: (
                    hashlib.sha256(seed + item.candidate_id.bytes).digest(),
                    item.candidate_id.bytes,
                ),
            )[:protocol.sample_size]
            expected = {
                item.candidate_id: position
                for position, item in enumerate(ranked, 1)
            }
        sample_valid = seed_valid and all(
            item.selected_position == expected.get(item.candidate_id)
            for item in cases
        )
        population_valid = (
            protocol.population_count == len(cases)
            and protocol.population_sha256 == population_hash
        )
        manifest_valid = (
            manifest_hash is not None
            and protocol.manifest_sha256 == manifest_hash
        )
        return HiddenShadowProtocolIntegrityResponse(
            protocol_id=protocol.id,
            consistent=(metadata_valid and population_valid
                        and manifest_valid and sample_valid),
            population_valid=population_valid,
            manifest_valid=manifest_valid,
            sample_valid=sample_valid,
            metadata_valid=metadata_valid,
            population_count=len(cases),
            recorded_population_count=protocol.population_count,
            population_sha256=population_hash,
            recorded_population_sha256=protocol.population_sha256,
            manifest_sha256=manifest_hash,
            recorded_manifest_sha256=protocol.manifest_sha256,
        )

    def register(
        self,
        *,
        project_id: UUID,
        creator_id: UUID,
        request_id: UUID,
        window_start: datetime,
        window_end: datetime,
        sample_size: int,
        now: datetime | None = None,
    ) -> HiddenShadowProtocol:
        current = now or datetime.now(timezone.utc)
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise TGOAPIException(
                "影子验收时间必须包含时区", "HIDDEN_PROTOCOL_TIMEZONE",
                status_code=422,
            )
        start, end = _utc(window_start), _utc(window_end)
        if (
            not 1 <= sample_size <= 500
            or start < _utc(current) + timedelta(minutes=1)
            or not timedelta(0) < end - start <= timedelta(days=90)
        ):
            raise TGOAPIException(
                "影子验收须预登记未来 90 天内的有效时间窗",
                "HIDDEN_PROTOCOL_WINDOW_INVALID", status_code=409,
            )
        fingerprint = _request_fingerprint(start, end, sample_size)

        def prior() -> HiddenShadowProtocol | None:
            row = self._db.scalar(select(HiddenShadowProtocol).where(
                HiddenShadowProtocol.project_id == project_id,
                HiddenShadowProtocol.request_id == request_id,
            ))
            if row is not None and (
                row.cancelled_at is not None
                or row.request_fingerprint != fingerprint
                or not _registration_matches(row)
            ):
                raise TGOAPIException(
                    "同一预登记请求编号不能更改抽样条件",
                    "HIDDEN_PROTOCOL_REQUEST_CONFLICT", status_code=409,
                )
            return row

        existing = prior()
        if existing is not None:
            return existing
        active_hidden = self._db.scalar(
            select(HiddenShadowProtocol.id).where(
                HiddenShadowProtocol.project_id == project_id,
                HiddenShadowProtocol.finalized_at.is_(None),
                HiddenShadowProtocol.cancelled_at.is_(None),
            )
        )
        active_legacy = self._db.scalar(
            select(ShadowReviewProtocol.id).where(
                ShadowReviewProtocol.project_id == project_id,
                ShadowReviewProtocol.finalized_at.is_(None),
            )
        )
        if active_hidden is not None or active_legacy is not None:
            raise TGOAPIException(
                "本项目已有未结束的影子验收预登记",
                "HIDDEN_PROTOCOL_ACTIVE", status_code=409,
            )
        random_seed = secrets.token_hex(16)
        row = HiddenShadowProtocol(
            project_id=project_id,
            creator_id=creator_id,
            request_id=request_id,
            request_fingerprint=fingerprint,
            window_start=start,
            window_end=end,
            sample_size=sample_size,
            algorithm=ALGORITHM,
            random_seed=random_seed,
            seed_sha256=hashlib.sha256(
                bytes.fromhex(random_seed)
            ).hexdigest(),
            created_at=_utc(current),
        )
        self._db.add(row)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is not None:
                return existing
            raise TGOAPIException(
                "影子验收预登记发生并发冲突",
                "HIDDEN_PROTOCOL_CONFLICT", status_code=409,
            ) from None
        return row

    def cancel(
        self, project_id: UUID, protocol_id: UUID, *,
        actor_id: UUID, now: datetime | None = None,
    ) -> HiddenShadowProtocol:
        row = self._db.scalar(select(HiddenShadowProtocol).where(
            HiddenShadowProtocol.id == protocol_id,
            HiddenShadowProtocol.project_id == project_id,
        ).with_for_update())
        if row is None:
            raise TGOAPIException(
                "独立影子验收预登记不存在",
                "HIDDEN_PROTOCOL_NOT_FOUND", status_code=404,
            )
        if row.cancelled_at is not None:
            return row
        if row.finalized_at is not None:
            raise TGOAPIException(
                "已冻结的影子验收不能撤销预登记",
                "HIDDEN_PROTOCOL_ALREADY_FROZEN", status_code=409,
            )
        row.cancelled_at = _utc(now or datetime.now(timezone.utc))
        row.cancelled_by = actor_id
        self._db.commit()
        return row

    def finalize(
        self,
        project_id: UUID,
        protocol_id: UUID,
        *,
        now: datetime | None = None,
    ) -> HiddenShadowProtocol:
        row = self._db.scalar(
            select(HiddenShadowProtocol).where(
                HiddenShadowProtocol.id == protocol_id,
                HiddenShadowProtocol.project_id == project_id,
            ).with_for_update()
        )
        if row is None:
            raise TGOAPIException(
                "独立影子验收预登记不存在",
                "HIDDEN_PROTOCOL_NOT_FOUND", status_code=404,
            )
        if row.cancelled_at is not None:
            raise TGOAPIException(
                "影子验收预登记已撤销",
                "HIDDEN_PROTOCOL_CANCELLED", status_code=409,
            )
        if not _registration_matches(row):
            raise TGOAPIException(
                "影子验收预登记条件或种子承诺已变化",
                "HIDDEN_PROTOCOL_REGISTRATION_CHANGED", status_code=409,
            )
        if row.finalized_at is not None:
            return row
        current = _utc(now or datetime.now(timezone.utc))
        if current < _utc(row.window_end):
            raise TGOAPIException(
                "影子验收时间窗尚未结束",
                "HIDDEN_PROTOCOL_NOT_READY", status_code=409,
            )
        if row.algorithm != ALGORITHM:
            raise TGOAPIException(
                "抽样算法版本不受当前服务支持",
                "HIDDEN_PROTOCOL_ALGORITHM_UNSUPPORTED", status_code=409,
            )
        stream = independent_shadow_comparisons(
            self._db, project_id,
            window_start=_utc(row.window_start),
            window_end=_utc(row.window_end),
        )
        try:
            comparisons = list(itertools.islice(stream, MAX_POPULATION + 1))
        finally:
            stream.close()
        if len(comparisons) > MAX_POPULATION:
            raise TGOAPIException(
                "候选数量超过单次冻结上限，请缩短预登记时间窗",
                "HIDDEN_PROTOCOL_TOO_LARGE", status_code=409,
            )
        comparisons.sort(key=lambda item: item.candidate_id.bytes)
        candidate_ids = {item.candidate_id for item in comparisons}
        if len(candidate_ids) != len(comparisons):
            raise TGOAPIException(
                "影子候选编号重复，无法冻结",
                "HIDDEN_PROTOCOL_DUPLICATE", status_code=409,
            )
        try:
            population_hash, manifest_hash = _manifest_digests(comparisons)
        except (TypeError, ValueError):
            raise TGOAPIException(
                "影子候选证据摘要无效，无法冻结",
                "HIDDEN_PROTOCOL_EVIDENCE_INVALID", status_code=409,
            ) from None
        seed = bytes.fromhex(row.random_seed)
        chosen = sorted(
            comparisons,
            key=lambda item: (
                hashlib.sha256(seed + item.candidate_id.bytes).digest(),
                item.candidate_id.bytes,
            ),
        )[:row.sample_size]
        positions = {
            item.candidate_id: position
            for position, item in enumerate(chosen, 1)
        }
        self._db.add_all(
            HiddenShadowProtocolCase(
                protocol_id=row.id,
                candidate_id=item.candidate_id,
                visitor_id=item.visitor_id,
                staff_delivery_id=item.staff_delivery_id,
                source_message_id=item.source_message_id,
                source_content_sha256=item.source_content_sha256,
                candidate_sha256=item.candidate_sha256,
                staff_reply_sha256=item.staff_reply_sha256,
                candidate_completed_at=item.candidate_completed_at,
                staff_reply_created_at=item.staff_reply_created_at,
                selected_position=positions.get(item.candidate_id),
            )
            for item in comparisons
        )
        row.population_count = len(comparisons)
        row.sample_count = len(chosen)
        row.population_sha256 = population_hash
        row.manifest_sha256 = manifest_hash
        row.finalized_at = current
        self._db.commit()
        return row
