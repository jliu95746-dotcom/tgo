"""Pre-register and freeze one official G4 shadow review window."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.shadow_review_batch import (
    ShadowReviewBatch,
    ShadowReviewBatchItem,
)
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol,
    ShadowReviewProtocolCandidate,
)
from app.schemas.shadow_sample import (
    ShadowProtocolCandidateItem,
    ShadowProtocolCandidatePage,
    ShadowProtocolCreateRequest,
    ShadowProtocolList,
    ShadowProtocolResponse,
    ShadowProtocolStatus,
    ShadowProtocolIntegrityResponse,
)
from app.services.shadow_sample_selection import (
    SAMPLE_ALGORITHM,
    WECOM_SAMPLE_ALGORITHM,
    eligible_shadow_cases_query,
    rank_shadow_population,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    # SQLite test storage drops tzinfo; PostgreSQL keeps timezone-aware values.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _response(row: ShadowReviewProtocol) -> ShadowProtocolResponse:
    status: ShadowProtocolStatus = (
        "scheduled"
        if row.finalized_at is None
        else "frozen"
        if row.batch_id is not None
        else "empty"
    )
    return ShadowProtocolResponse(
        id=row.id,
        request_id=row.request_id,
        created_at=row.created_at,
        window_start=row.window_start,
        window_end=row.window_end,
        sample_size=row.sample_size,
        algorithm=row.algorithm,
        target_channel="wecom_kf"
        if row.algorithm == WECOM_SAMPLE_ALGORITHM
        else "all",
        seed_sha256=hashlib.sha256(bytes.fromhex(row.random_seed)).hexdigest(),
        status=status,
        finalized_at=row.finalized_at,
        batch_id=row.batch_id,
        population_count=row.population_count,
        population_sha256=row.population_sha256,
        manifest_sha256=row.manifest_sha256,
    )


class ShadowReviewProtocolService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def verify_integrity(
        self, project_id: UUID, protocol_id: UUID
    ) -> ShadowProtocolIntegrityResponse:
        from app.services.shadow_manifest_integrity import (
            verify_shadow_manifest,
        )

        return verify_shadow_manifest(self._db, project_id, protocol_id)

    def get(self, project_id: UUID) -> ShadowProtocolResponse | None:
        row = self._db.scalar(
            select(ShadowReviewProtocol)
            .where(ShadowReviewProtocol.project_id == project_id)
            .order_by(
                ShadowReviewProtocol.created_at.desc(),
                ShadowReviewProtocol.id.desc(),
            )
            .limit(1)
        )
        return _response(row) if row is not None else None

    def list(self, project_id: UUID, *, limit: int) -> ShadowProtocolList:
        rows = self._db.scalars(
            select(ShadowReviewProtocol)
            .where(ShadowReviewProtocol.project_id == project_id)
            .order_by(
                ShadowReviewProtocol.created_at.desc(),
                ShadowReviewProtocol.id.desc(),
            )
            .limit(limit + 1)
        ).all()
        return ShadowProtocolList(
            items=tuple(_response(row) for row in rows[:limit]),
            has_more=len(rows) > limit,
        )

    def list_candidates(
        self,
        project_id: UUID,
        protocol_id: UUID,
        *,
        limit: int,
        after_draft_id: UUID | None = None,
    ) -> ShadowProtocolCandidatePage:
        protocol = self._db.scalar(
            select(ShadowReviewProtocol).where(
                ShadowReviewProtocol.id == protocol_id,
                ShadowReviewProtocol.project_id == project_id,
            )
        )
        if protocol is None:
            raise TGOAPIException(
                "正式影子验收方案不存在",
                "SHADOW_PROTOCOL_NOT_FOUND",
                status_code=404,
            )
        if protocol.finalized_at is None:
            raise TGOAPIException(
                "正式影子验收候选清单尚未冻结",
                "SHADOW_PROTOCOL_NOT_READY",
                status_code=409,
            )
        if after_draft_id is not None:
            cursor_exists = self._db.scalar(
                select(ShadowReviewProtocolCandidate.draft_id).where(
                    ShadowReviewProtocolCandidate.protocol_id == protocol.id,
                    ShadowReviewProtocolCandidate.draft_id == after_draft_id,
                )
            )
            if cursor_exists is None:
                raise TGOAPIException(
                    "候选清单游标不存在",
                    "SHADOW_PROTOCOL_CURSOR_NOT_FOUND",
                    status_code=404,
                )
        candidates = (
            select(
                ShadowReviewProtocolCandidate.draft_id,
                ShadowReviewProtocolCandidate.visitor_id,
                ShadowReviewBatchItem.position.label("selected_position"),
            )
            .outerjoin(
                ShadowReviewBatchItem,
                and_(
                    ShadowReviewBatchItem.batch_id == protocol.batch_id,
                    ShadowReviewBatchItem.draft_id
                    == ShadowReviewProtocolCandidate.draft_id,
                ),
            )
            .where(ShadowReviewProtocolCandidate.protocol_id == protocol.id)
        )
        if after_draft_id is not None:
            candidates = candidates.where(
                ShadowReviewProtocolCandidate.draft_id > after_draft_id
            )
        rows = self._db.execute(
            candidates.order_by(ShadowReviewProtocolCandidate.draft_id).limit(
                limit + 1
            )
        ).all()
        visible = rows[:limit]
        return ShadowProtocolCandidatePage(
            protocol_id=protocol.id,
            total_candidates=protocol.population_count or 0,
            population_sha256=protocol.population_sha256 or "",
            items=tuple(
                ShadowProtocolCandidateItem(
                    draft_id=row.draft_id,
                    visitor_id=row.visitor_id,
                    selected_position=row.selected_position,
                )
                for row in visible
            ),
            next_cursor=visible[-1].draft_id if len(rows) > limit else None,
        )

    def register(
        self,
        project_id: UUID,
        creator_id: UUID,
        request: ShadowProtocolCreateRequest,
    ) -> ShadowProtocolResponse:
        excluded = {"request_id"}
        if request.target_channel is None:
            # Preserve the fingerprint of requests issued before channel selection.
            excluded.add("target_channel")
        fingerprint = hashlib.sha256(
            request.model_dump_json(exclude=excluded).encode()
        ).hexdigest()

        def prior() -> ShadowReviewProtocol | None:
            row = self._db.scalar(
                select(ShadowReviewProtocol).where(
                    ShadowReviewProtocol.project_id == project_id,
                    ShadowReviewProtocol.request_id == request.request_id,
                )
            )
            if row is not None and row.request_fingerprint != fingerprint:
                raise TGOAPIException(
                    "同一正式影子验收请求编号不能用于不同条件",
                    "SHADOW_PROTOCOL_REQUEST_CONFLICT",
                    status_code=409,
                )
            return row

        existing = prior()
        if existing is not None:
            return _response(existing)
        active = self._db.scalar(
            select(ShadowReviewProtocol.id).where(
                ShadowReviewProtocol.project_id == project_id,
                ShadowReviewProtocol.finalized_at.is_(None),
            )
        )
        if active is not None:
            raise TGOAPIException(
                "本项目已有未结束的正式影子验收方案",
                "SHADOW_PROTOCOL_ACTIVE",
                status_code=409,
            )
        now = _utc_now()
        start = _utc(request.window_start)
        end = _utc(request.window_end)
        if start < now + timedelta(minutes=1):
            raise TGOAPIException(
                "正式影子验收必须至少提前一分钟预登记",
                "SHADOW_PROTOCOL_NOT_FUTURE",
                status_code=409,
            )
        row = ShadowReviewProtocol(
            project_id=project_id,
            creator_id=creator_id,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            window_start=start,
            window_end=end,
            sample_size=request.sample_size,
            algorithm=(
                WECOM_SAMPLE_ALGORITHM
                if request.target_channel == "wecom_kf"
                else SAMPLE_ALGORITHM
            ),
            random_seed=secrets.token_hex(16),
            created_at=now,
        )
        self._db.add(row)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is not None:
                return _response(existing)
            active = self._db.scalar(
                select(ShadowReviewProtocol.id).where(
                    ShadowReviewProtocol.project_id == project_id,
                    ShadowReviewProtocol.finalized_at.is_(None),
                )
            )
            if active is not None:
                raise TGOAPIException(
                    "本项目已有未结束的正式影子验收方案",
                    "SHADOW_PROTOCOL_ACTIVE",
                    status_code=409,
                )
            raise
        return _response(row)

    def finalize(
        self, project_id: UUID, protocol_id: UUID | None = None
    ) -> ShadowProtocolResponse:
        query = select(ShadowReviewProtocol).where(
            ShadowReviewProtocol.project_id == project_id
        )
        if protocol_id is not None:
            query = query.where(ShadowReviewProtocol.id == protocol_id)
        else:
            query = query.order_by(
                ShadowReviewProtocol.created_at.desc(),
                ShadowReviewProtocol.id.desc(),
            ).limit(1)
        row = self._db.scalar(query.with_for_update())
        if row is None:
            raise TGOAPIException(
                "本项目尚未预登记正式影子验收方案",
                "SHADOW_PROTOCOL_NOT_FOUND",
                status_code=404,
            )
        if row.finalized_at is not None:
            return _response(row)
        now = _utc_now()
        if now < _utc(row.window_end):
            raise TGOAPIException(
                "正式影子验收时间窗尚未结束",
                "SHADOW_PROTOCOL_NOT_READY",
                status_code=409,
            )
        if row.algorithm not in {SAMPLE_ALGORITHM, WECOM_SAMPLE_ALGORITHM}:
            raise TGOAPIException(
                "预登记抽样算法版本不受当前服务支持",
                "SHADOW_PROTOCOL_ALGORITHM_UNSUPPORTED",
                status_code=409,
            )
        eligible = eligible_shadow_cases_query(
            project_id,
            window_start=_utc(row.window_start),
            window_end=_utc(row.window_end),
            target_channel=(
                "wecom_kf" if row.algorithm == WECOM_SAMPLE_ALGORITHM else None
            ),
        ).subquery()
        self._db.execute(
            insert(ShadowReviewProtocolCandidate).from_select(
                ["protocol_id", "draft_id", "visitor_id"],
                select(literal(row.id), eligible.c.id, eligible.c.visitor_id),
            )
        )
        candidate_rows = self._db.execute(
            select(
                ShadowReviewProtocolCandidate.draft_id,
                ShadowReviewProtocolCandidate.visitor_id,
            )
            .where(ShadowReviewProtocolCandidate.protocol_id == row.id)
            .order_by(ShadowReviewProtocolCandidate.draft_id)
            .execution_options(stream_results=True)
        ).yield_per(1000)
        population = rank_shadow_population(
            ((item.draft_id, item.visitor_id) for item in candidate_rows),
            sample_size=row.sample_size,
            random_seed=row.random_seed,
        )
        if population.chosen:
            batch = ShadowReviewBatch(
                project_id=project_id,
                creator_id=row.creator_id,
                request_id=uuid4(),
                request_fingerprint=row.request_fingerprint,
                window_start=row.window_start,
                window_end=row.window_end,
                random_seed=row.random_seed,
                algorithm=row.algorithm,
                population_sha256=population.sha256,
                population_count=population.count,
                requested_size=row.sample_size,
                sample_count=len(population.chosen),
                created_at=now,
            )
            self._db.add(batch)
            self._db.flush()
            self._db.add_all(
                ShadowReviewBatchItem(
                    batch_id=batch.id,
                    position=position,
                    draft_id=draft_id,
                    visitor_id=visitor_id,
                )
                for position, (_, draft_id, visitor_id) in enumerate(
                    population.chosen, 1
                )
            )
            row.batch_id = batch.id
        row.population_count = population.count
        row.population_sha256 = population.sha256
        row.manifest_sha256 = population.manifest_sha256
        row.finalized_at = now
        self._db.commit()
        return _response(row)
