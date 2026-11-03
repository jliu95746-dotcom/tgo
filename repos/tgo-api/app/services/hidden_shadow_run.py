"""Generate private, knowledge-only candidates for verified WeCom messages."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_run import HiddenShadowRun
from app.services.ai_client import AIServiceClient
from app.services.current_knowledge import read_knowledge_evidence
from app.services.shadow_source_proof import (
    ShadowIMLookup, verify_shadow_source,
)


MAX_ATTEMPTS = 3
LEASE_DURATION = timedelta(minutes=3)
MAX_ANSWER_LENGTH = 4000
GENERATION_VERSION = "wecom-kf-knowledge-only-v1"


class HiddenShadowRunService:
    """Persist a candidate outside staff and customer delivery."""

    def __init__(
        self,
        db: Session,
        *,
        ai_client: AIServiceClient,
        im_client: ShadowIMLookup,
    ) -> None:
        self._db = db
        self._ai_client = ai_client
        self._im_client = im_client

    def _find(
        self,
        project_id: UUID,
        platform_id: UUID,
        source_message_id: str,
    ) -> HiddenShadowRun | None:
        return cast(HiddenShadowRun | None, self._db.scalar(
            select(HiddenShadowRun).where(
                HiddenShadowRun.project_id == project_id,
                HiddenShadowRun.platform_id == platform_id,
                HiddenShadowRun.source_message_id == source_message_id,
            )
        ))

    async def generate(
        self,
        *,
        project_id: UUID,
        platform_id: UUID,
        visitor_id: UUID,
        agent_id: UUID | None,
        source_message_id: str,
        customer_message: str,
    ) -> HiddenShadowRun | None:
        """Return the candidate or None when IM proof is unavailable."""
        if not source_message_id or len(source_message_id) > 255:
            return None
        proof = await verify_shadow_source(
            im_client=self._im_client,
            visitor_id=visitor_id,
            source_message_id=source_message_id,
            source_client_msg_no=source_message_id,
            customer_message=customer_message,
            message_type=1,
        )
        content_hash = sha256(customer_message.strip().encode()).hexdigest()
        if (
            proof.status != "verified"
            or proof.client_msg_no != source_message_id
            or proof.content_sha256 != content_hash
            or not proof.message_seq
            or proof.message_seq <= 0
        ):
            return None

        now = datetime.now(timezone.utc)
        row = self._find(project_id, platform_id, source_message_id)
        if row is None:
            row = HiddenShadowRun(
                project_id=project_id,
                platform_id=platform_id,
                visitor_id=visitor_id,
                source_message_id=source_message_id,
                source_message_seq=proof.message_seq,
                source_verified_at=proof.checked_at,
                source_content_sha256=content_hash,
                agent_id=agent_id,
                generation_version=GENERATION_VERSION,
                status="failed",
                attempt_count=0,
            )
            self._db.add(row)
            try:
                self._db.commit()
            except IntegrityError:
                self._db.rollback()
                row = self._find(project_id, platform_id, source_message_id)
                if row is None:
                    raise

        if (
            row.visitor_id != visitor_id
            or row.source_content_sha256 != content_hash
            or row.source_message_seq != proof.message_seq
        ):
            raise TGOAPIException(
                "影子评测消息来源发生冲突",
                "HIDDEN_SHADOW_SOURCE_CONFLICT",
                status_code=409,
            )
        if row.status in ("ready", "blocked"):
            return row
        lease_until = row.lease_until
        if lease_until is not None and lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=timezone.utc)
        if (
            row.attempt_count >= MAX_ATTEMPTS
            or (row.status == "running" and lease_until is not None
                and lease_until > now)
        ):
            return row

        lease_token = uuid4()
        claimed = self._db.execute(
            update(HiddenShadowRun)
            .where(
                HiddenShadowRun.id == row.id,
                HiddenShadowRun.status.in_(("failed", "running")),
                HiddenShadowRun.attempt_count < MAX_ATTEMPTS,
                (HiddenShadowRun.lease_until.is_(None)
                 | (HiddenShadowRun.lease_until < now)
                 | (HiddenShadowRun.status == "failed")),
            )
            .values(
                status="running",
                attempt_count=HiddenShadowRun.attempt_count + 1,
                lease_token=lease_token,
                lease_until=now + LEASE_DURATION,
                failure_code=None,
            )
            .execution_options(synchronize_session=False)
        ).rowcount
        self._db.commit()
        if not claimed:
            return self._find(project_id, platform_id, source_message_id)

        try:
            response = await self._ai_client.run_supervisor_agent(
                message=customer_message,
                project_id=str(project_id),
                agent_id=str(agent_id) if agent_id else None,
                stream=False,
                knowledge_channel="wecom_kf",
                require_current_knowledge=True,
                knowledge_only=True,
                enable_memory=False,
            )
            evidence = read_knowledge_evidence(
                response.get("knowledge_evidence"),
                project_id=str(project_id),
                channel="wecom_kf",
            )
            evidence_hash = sha256(json.dumps(
                evidence.model_dump(mode="json"),
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode()).hexdigest()
            metadata = response.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            raw_model = metadata.get("model_name")
            model_name = (
                raw_model if isinstance(raw_model, str)
                and 0 < len(raw_model) <= 255 else None
            )
            raw_prompt_hash = metadata.get("configured_prompt_sha256")
            prompt_hash = (
                raw_prompt_hash if isinstance(raw_prompt_hash, str)
                and re.fullmatch(r"[0-9a-f]{64}", raw_prompt_hash) else None
            )
            try:
                execution_id = UUID(str(response.get("execution_id")))
            except (ValueError, TypeError):
                execution_id = None
            content = response.get("content")
            ready = (
                evidence.status == "matched"
                and bool(evidence.documents)
                and not evidence.tool_results
                and isinstance(content, str)
                and bool(content.strip())
                and len(content) <= MAX_ANSWER_LENGTH
            )
            answer = (
                content.strip() if ready and isinstance(content, str) else None
            )
            values = {
                "status": "ready" if ready else "blocked",
                "knowledge_status": evidence.status,
                "knowledge_evidence_sha256": evidence_hash,
                "model_name": model_name,
                "configured_prompt_sha256": prompt_hash,
                "ai_execution_id": execution_id,
                "answer": answer,
                "answer_sha256": (
                    sha256(answer.encode()).hexdigest() if answer else None
                ),
                "failure_code": None if ready else "KNOWLEDGE_NOT_GROUNDED",
                "completed_at": datetime.now(timezone.utc),
            }
        except Exception:
            values = {
                "status": "failed",
                "knowledge_status": None,
                "knowledge_evidence_sha256": None,
                "model_name": None,
                "configured_prompt_sha256": None,
                "ai_execution_id": None,
                "answer": None,
                "answer_sha256": None,
                "failure_code": "AI_RUN_FAILED",
                "completed_at": None,
            }
        self._db.execute(
            update(HiddenShadowRun)
            .where(HiddenShadowRun.id == row.id,
                   HiddenShadowRun.lease_token == lease_token)
            .values(**values, lease_token=None, lease_until=None)
            .execution_options(synchronize_session=False)
        )
        self._db.commit()
        return self._find(project_id, platform_id, source_message_id)
