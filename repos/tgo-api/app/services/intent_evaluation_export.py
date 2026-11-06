"""Export only authorized, already-persisted G3 prediction fields."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.message_intent_result import MessageIntentResult
from app.schemas.intent_evaluation_export import (
    IntentEvaluationExportRequest,
    IntentEvaluationPrediction,
)


class IntentEvaluationExportService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def export(
        self, project_id: UUID, request: IntentEvaluationExportRequest
    ) -> str:
        """Return all requested rows or none, in the caller's case order."""
        rows = self._db.execute(
            select(
                MessageIntentResult.id,
                MessageIntentResult.project_id,
                MessageIntentResult.platform_id,
                MessageIntentResult.analysis_mode,
                MessageIntentResult.intent,
                MessageIntentResult.confidence,
                MessageIntentResult.recommended_route,
                MessageIntentResult.need_human,
                MessageIntentResult.taxonomy_version,
                MessageIntentResult.classifier_version,
                MessageIntentResult.policy_version,
            ).where(
                MessageIntentResult.project_id == project_id,
                MessageIntentResult.platform_id == request.platform_id,
                MessageIntentResult.analysis_mode == request.analysis_mode,
                MessageIntentResult.id.in_(request.case_ids),
            )
        ).all()
        by_id = {row.id: row for row in rows}
        if (
            len(rows) != len(request.case_ids)
            or len(by_id) != len(rows)
            or set(by_id) != set(request.case_ids)
            or any(
                row.project_id != project_id
                or row.platform_id != request.platform_id
                or row.analysis_mode != request.analysis_mode
                for row in rows
            )
        ):
            raise TGOAPIException(
                "所选分类结果不完整或不可访问",
                "INTENT_EVALUATION_CASES_NOT_FOUND",
                status_code=404,
            )
        try:
            predictions = tuple(
                IntentEvaluationPrediction.model_validate(
                    {
                        "case_id": str(case_id),
                        "intent": by_id[case_id].intent,
                        "confidence": by_id[case_id].confidence,
                        "recommended_route": by_id[case_id].recommended_route,
                        "need_human": by_id[case_id].need_human,
                        "taxonomy_version": by_id[case_id].taxonomy_version,
                        "classifier_version": by_id[
                            case_id
                        ].classifier_version,
                        "policy_version": by_id[case_id].policy_version,
                    }
                )
                for case_id in request.case_ids
            )
        except (KeyError, ValidationError) as exc:
            raise TGOAPIException(
                "所选分类结果不符合 G3 评测格式",
                "INTENT_EVALUATION_CASES_INVALID",
                status_code=409,
            ) from exc
        if (
            len(
                {
                    (item.classifier_version, item.policy_version)
                    for item in predictions
                }
            )
            != 1
        ):
            raise TGOAPIException(
                "所选分类结果存在混用的分类器或策略版本",
                "INTENT_EVALUATION_VERSION_MISMATCH",
                status_code=409,
            )
        return "\n".join(item.model_dump_json() for item in predictions) + "\n"
