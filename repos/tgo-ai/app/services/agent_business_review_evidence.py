"""Fresh checks of a completed test, separate from its execution lease."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select

from app.config import settings
from app.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_connections import BusinessConnectionOwner
from app.schemas.agent_business_reviews import (
    BusinessTestEvidenceQuery,
    ReviewIssue,
)
from app.schemas.agent_business_trials import BusinessTrialReport
from app.services.agent_business_connections import fresh
from app.services.agent_business_trials import AgentBusinessTrialService, utc
from app.services.agent_trial_fingerprint import private_digest

CurrentContext = tuple[AgentBusinessConnection, BusinessConnectionOwner, str]


@dataclass(frozen=True)
class ReviewEvidence:
    status: Literal["current", "changed", "not_eligible", "unavailable"]
    issues: list[ReviewIssue]
    valid_until: datetime | None


def report_digest(report: BusinessTrialReport) -> str:
    return private_digest(
        report.model_dump(mode="json", exclude={"assessment", "can_enable"})
    )


def validity(report: BusinessTrialReport) -> datetime:
    observed = report.completed_at or report.created_at
    result = utc(observed) + timedelta(
        hours=settings.agent_trial_review_validity_hours
    )
    if report.receipt:
        result = min(result, report.receipt.configuration_test_valid_until)
    return result


class BusinessReviewEvidence:
    def __init__(self, trials: AgentBusinessTrialService):
        self.trials = trials

    async def latest_report(self, report: BusinessTrialReport) -> UUID | None:
        return cast(
            UUID | None,
            await self.trials.db.scalar(
                select(AgentBusinessTrial.id)
                .where(
                    AgentBusinessTrial.project_id == report.project_id,
                    AgentBusinessTrial.actor_id == report.actor_id,
                    AgentBusinessTrial.agent_id == report.agent_id,
                    AgentBusinessTrial.connection_id == report.connection_id,
                    AgentBusinessTrial.operation == report.operation,
                    AgentBusinessTrial.deleted_at.is_(None),
                )
                .order_by(
                    AgentBusinessTrial.created_at.desc(),
                    AgentBusinessTrial.id.desc(),
                )
                .limit(1)
            ),
        )

    async def assess(
        self,
        report: BusinessTrialReport,
        *,
        context: CurrentContext | None = None,
    ) -> ReviewEvidence:
        deadline = validity(report)
        result = report.receipt
        if report.status != "completed" or result is None:
            return ReviewEvidence(
                "not_eligible", ["test_not_completed"], deadline
            )
        if (
            result.outcome != "passed"
            or result.error_code is not None
            or result.summary is None
        ):
            return ReviewEvidence(
                "not_eligible", ["test_not_passed"], deadline
            )
        summary = result.summary
        customer_mapping_invalid = (
            report.operation == "customer_profile_query"
            and (
                summary.service_tags is None
                or summary.segment == "结果标签需配置映射"
                or "结果标签需配置映射" in summary.service_tags
            )
        ) or (
            report.operation == "membership_query"
            and (
                not summary.tier
                or summary.tier == "结果标签需配置映射"
                or summary.points_balance is None
            )
        )
        if (
            result.summary.status == "结果标签需配置映射"
            or customer_mapping_invalid
            or report.operation == "logistics_query"
            and (
                not result.summary.carrier
                or not result.summary.tracking_no_masked
                or result.summary.carrier == "结果标签需配置映射"
            )
            or result.summary.amount_minor is not None
            and result.summary.currency is None
        ):
            return ReviewEvidence(
                "changed", ["result_mapping_unverified"], deadline
            )
        if deadline <= datetime.now(UTC):
            return ReviewEvidence("changed", ["report_expired"], deadline)
        if await self.latest_report(report) != report.id:
            return ReviewEvidence("changed", ["superseded"], deadline)
        try:
            (
                binding,
                owner,
                fingerprint,
            ) = context or await self.trials._current(
                report.project_id,
                report.actor_id,
                report.agent_id,
                report.connection_id,
                report.operation,
                require_paused=False,
            )
            proof = next(
                (
                    row
                    for row in owner.configuration_tests
                    if row.operation == report.operation
                ),
                None,
            )
            if (
                binding.id != result.binding_id
                or binding.revision != result.binding_revision
                or not binding.enabled
                or report.operation not in binding.operations
                or fingerprint != report.configuration_fingerprint
                or owner.revision != result.expected_revision
                or owner.configuration_revision
                != result.configuration_revision
                or owner.authorization_revision
                != result.authorization_revision
                or owner.resource_revision != result.resource_revision
                or not owner.enabled
                or owner.mode != "http"
                or not fresh(owner.checked_at)
                or proof is None
                or proof.status != "current"
                or proof.report_id != result.configuration_test_id
                or proof.valid_until != result.configuration_test_valid_until
            ):
                return ReviewEvidence(
                    "changed", ["configuration_changed"], deadline
                )
            query = BusinessTestEvidenceQuery(
                project_id=report.project_id,
                requested_by=report.actor_id,
                connection_id=report.connection_id,
                configuration_revision=result.configuration_revision,
                customer_evidence=result.customer_evidence,
                observed_at=result.checked_at,
            )
            evidence = (
                await self.trials.owner.get_agent_business_test_evidence(query)
            )
            if evidence.model_dump(
                include=set(type(query).model_fields)
            ) != query.model_dump() or not fresh(evidence.checked_at):
                return ReviewEvidence(
                    "unavailable", ["evidence_unavailable"], deadline
                )
            if evidence.status != "current":
                return ReviewEvidence(
                    "changed", list(evidence.issues), deadline
                )
            if deadline <= datetime.now(UTC):
                return ReviewEvidence("changed", ["report_expired"], deadline)
            return ReviewEvidence("current", [], deadline)
        except AuthorizationError:
            raise
        except (ConflictError, NotFoundError):
            return ReviewEvidence(
                "changed", ["configuration_changed"], deadline
            )
        except Exception:
            return ReviewEvidence(
                "unavailable", ["evidence_unavailable"], deadline
            )
