"""Transfer to human service for visitor assignment."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import JsonValue

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.services.visitor_mutation_access import VisitorMutationScope, changed
from app.services.staff_queue_access import StaffQueueScope
from app.services.staff_session_access import StaffSessionScope
from app.services.session_command_delivery import record_transfer
from app.services.assignment_channel_delivery import (
    deliver_assignment_channel,
    stage_assignment_channel,
)
from app.services.profile_access import OperationGuard
from app.schemas.staff_data_access import StaffDataAccess
from app.core.logging import get_logger
from app.models import (
    Visitor,
    VisitorSession,
    VisitorAssignmentHistory,
    VisitorAssignmentRule,
    VisitorWaitingQueue,
    WaitingStatus,
    QueueSource,
    AssignmentSource,
    SessionStatus,
    Staff,
)
from app.services.queue_lifecycle import (
    assign_waiting_entries,
    cancel_entry,
    lock_waiting_entry,
    read_waiting_entry,
)
from app.services.queue_events import schedule_queue_update
from app.services.staff_assignment_binding import (
    lock_reception_project,
    reception_binding,
)
from app.services.staff_assignment_capacity import reserve_staff_seat
from app.services.staff_assignment_guard import (
    AssignmentConfiguration,
    assignment_configuration_is_current,
    capture_assignment_configuration,
    read_assignment_rule,
)
from app.services.staff_assignment_response import (
    LLMStaffAssignmentResult,
    assignment_response_content,
    assignment_selection,
)
from app.services.visitor_notifications import notify_visitor_profile_updated

logger = get_logger("services.transfer")


@dataclass
class TransferResult:
    """Result of transfer to human operation."""

    success: bool
    session: Optional[VisitorSession]
    assignment_history: Optional[VisitorAssignmentHistory]
    assigned_staff_id: Optional[UUID]
    candidate_staff_ids: Optional[List[UUID]]
    waiting_queue: Optional[VisitorWaitingQueue]
    queue_position: Optional[int]
    message: str
    operation_guard: OperationGuard | None = None


@dataclass
class StaffCandidate:
    """Staff candidate for assignment."""

    id: UUID
    name: Optional[str]
    nickname: Optional[str]
    description: Optional[str]
    status: str
    current_chat_count: int = 0


@dataclass
class StaffAssignmentResult:
    """Result of staff assignment operation."""

    assigned_staff_id: Optional[UUID]
    candidate_staff_ids: List[UUID]
    llm_response: Optional[str] = None
    llm_reasoning: Optional[str] = None
    candidate_scores: Optional[dict[str, JsonValue]] = None
    model_used: Optional[str] = None
    prompt_used: Optional[str] = None
    configuration: AssignmentConfiguration | None = None


async def transfer_to_staff(
    db: Session,
    visitor_id: UUID,
    project_id: UUID,
    source: AssignmentSource = AssignmentSource.MANUAL,
    visitor_message: Optional[str] = None,
    assigned_by_staff_id: Optional[UUID] = None,
    target_staff_id: Optional[UUID] = None,
    session_id: Optional[UUID] = None,
    platform_id: Optional[UUID] = None,
    notes: Optional[str] = None,
    skip_queue_status_check: bool = False,
    auto_commit: bool = True,
    ai_disabled: Optional[bool] = None,
    add_to_queue_if_no_staff: bool = True,
    send_notification: bool = True,
    expected_queue_entry_id: Optional[UUID] = None,
    expected_unassigned: bool = False,
    mutation_scope: VisitorMutationScope | None = None,
    queue_scope: StaffQueueScope | None = None,
    session_scope: StaffSessionScope | None = None,
    operation_id: UUID | None = None,
) -> TransferResult:
    (
        "\n"
        "    Transfer a visitor to staff service.\n"
        "    \n"
        "    Assignment logic:\n"
        "    1. If target_staff_id specified, assign directly\n"
        "    2. Otherwise, get available staff candidates based on rules\n"
        "    3. If candidates > 1 and LLM enabled, use LLM to select\n"
        "    4. If candidates > 1 and LLM disabled, use load balancing\n"
        "    5. If candidates = 1, assign directly\n"
        "    6. If candidates = 0 and add_to_queue_if_no_staff=True, add to "
        "waiting queue\n"
        "    \n"
        "    Args:\n"
        "        db: Database session\n"
        "        visitor_id: ID of the visitor to transfer\n"
        "        project_id: ID of the project\n"
        "        source: Source of the transfer (MANUAL, LLM, RULE, "
        "TRANSFER)\n"
        "        visitor_message: Message that triggered the transfer\n"
        "        assigned_by_staff_id: Staff who initiated the transfer (for "
        "manual transfers)\n"
        "        target_staff_id: Specific staff to assign (optional)\n"
        "        session_id: Existing session ID (optional)\n"
        "        platform_id: Platform ID for new sessions\n"
        "        notes: Additional notes\n"
        "        skip_queue_status_check: Skip visitor status check (for "
        "queue processing)\n"
        "        auto_commit: Whether to auto-commit changes (default True)\n"
        "        ai_disabled: Whether to disable AI responses (None=keep "
        "current, True=disable, False=enable)\n"
        "        add_to_queue_if_no_staff: Whether to add to waiting queue "
        "if no staff available (default True)\n"
        "        send_notification: Whether to send staff assigned system "
        "message (default True)\n"
        "        \n"
        "    Returns:\n"
        "        TransferResult with success status and related objects\n"
        "    "
    )
    if mutation_scope is not None:
        if (
            mutation_scope.visitor_id != visitor_id
            or mutation_scope.access.project_id != project_id
            or mutation_scope.access.staff_id != target_staff_id
            or mutation_scope.access.staff_id != assigned_by_staff_id
            or mutation_scope.permission != "visitors:update"
            or not mutation_scope.claim
            or not expected_unassigned
            or not auto_commit
            or add_to_queue_if_no_staff
            or platform_id != mutation_scope.platform_id
        ):
            raise changed()
        mutation_scope.check(db)
    if session_scope is not None:
        if (
            mutation_scope is not None
            or queue_scope is not None
            or session_id != session_scope.session_id
            or visitor_id != session_scope.visitor_id
            or project_id != session_scope.access.project_id
            or platform_id != session_scope.platform_id
            or assigned_by_staff_id != session_scope.access.staff_id
            or target_staff_id is None
            or source != AssignmentSource.TRANSFER
            or not auto_commit
            or send_notification
        ):
            raise changed()
        session_scope.check(db, target_staff_id=target_staff_id)
    if queue_scope is not None:
        if (
            mutation_scope != queue_scope.customer
            or expected_queue_entry_id != queue_scope.entry_id
            or session_id != queue_scope.session_id
            or not queue_scope.accepting
        ):
            raise changed()
        queue_scope.check(db)
    elif mutation_scope is not None and expected_queue_entry_id is not None:
        raise changed()
    output_scope: VisitorMutationScope | None = None
    output_queue: StaffQueueScope | None = None
    output_session_guard: OperationGuard | None = None
    llm_response = None
    llm_reasoning = None
    candidate_staff_ids: List[UUID] = []
    candidate_scores: Optional[dict[str, JsonValue]] = None
    model_used: Optional[str] = None
    prompt_used: Optional[str] = None
    waiting_queue_entry: Optional[VisitorWaitingQueue] = None
    queue_position: Optional[int] = None

    try:
        if settings.SAAS_BILLING_ENABLED:
            from app.services.company_entitlements import require_human_service

            with db.no_autoflush:
                require_human_service(db, project_id, visitor_id)
        # Read-only preflight: no visitor/queue write locks across the AI wait.
        visitor = (
            db.query(Visitor)
            .execution_options(autoflush=False)
            .filter(
                Visitor.id == visitor_id,
                Visitor.project_id == project_id,
                Visitor.deleted_at.is_(None),
                Visitor.access_retired_at.is_(None),
            )
            .populate_existing()
            .first()
        )

        if not visitor:
            return TransferResult(
                success=False,
                session=None,
                assignment_history=None,
                assigned_staff_id=None,
                candidate_staff_ids=None,
                waiting_queue=None,
                queue_position=None,
                message="Visitor not found",
            )

        if expected_unassigned and visitor.service_status == "active":
            return TransferResult(
                success=False,
                session=None,
                assignment_history=None,
                assigned_staff_id=None,
                candidate_staff_ids=None,
                waiting_queue=None,
                queue_position=None,
                message="Visitor has already been claimed",
            )
        if expected_queue_entry_id is not None:
            with db.no_autoflush:
                expected_entry = read_waiting_entry(
                    db, visitor, expected_queue_entry_id
                )
            if expected_entry is None:
                return TransferResult(
                    success=False,
                    session=None,
                    assignment_history=None,
                    assigned_staff_id=None,
                    candidate_staff_ids=None,
                    waiting_queue=None,
                    queue_position=None,
                    message="Queue entry is no longer waiting or has expired",
                )

        # 1.5. Check if visitor can enter queue (only for non-direct
        # assignments)
        # Skip this check if:
        # - target_staff_id is specified (direct assignment)
        # - skip_queue_status_check is True (processing from queue)
        if (
            not target_staff_id
            and not skip_queue_status_check
            and not visitor.is_unassigned
        ):
            logger.info(
                (
                    f"Visitor "
                    f"{visitor_id}"
                    f" cannot enter queue, current status: "
                    f"{visitor.service_status}"
                )
            )
            return TransferResult(
                success=False,
                session=None,
                assignment_history=None,
                assigned_staff_id=None,
                candidate_staff_ids=None,
                waiting_queue=None,
                queue_position=None,
                message=(
                    f"Visitor cannot enter queue (current status: "
                    f"{visitor.service_status}"
                    f"). Only NEW or CLOSED status allowed."
                ),
            )

        binding = reception_binding(db, visitor, session_id)

        # 3. Get assignment rule for the project
        assignment_rule = (
            db.query(VisitorAssignmentRule)
            .execution_options(autoflush=False)
            .filter(
                VisitorAssignmentRule.project_id == project_id,
            )
            .first()
        )

        # 4. Determine staff assignment using assign_staff method

        with db.no_autoflush:
            assignment_result = await assign_staff(
                db=db,
                visitor_id=visitor_id,
                project_id=project_id,
                target_staff_id=target_staff_id,
                visitor_message=visitor_message,
                assignment_rule=assignment_rule,
                strict_target=(
                    mutation_scope is not None or session_scope is not None
                ),
                lock_assignment_configuration=False,
            )

        # Final writes lock project, visitor, then their resources.
        if settings.SAAS_BILLING_ENABLED:
            from app.services.company_membership import lock_company
            from app.services.company_entitlements import require_human_service

            lock_company(db, project_id)
            require_human_service(db, project_id, visitor_id)
        lock_reception_project(db, project_id)
        session: VisitorSession
        if mutation_scope is not None:
            visitor = mutation_scope.check(db, lock=True)
            if queue_scope is not None:
                expected_entry, _ = queue_scope.check(db, lock=True)
            if (
                assignment_result.assigned_staff_id
                != mutation_scope.access.staff_id
            ):
                raise changed()
        elif session_scope is not None:
            session = session_scope.check(
                db, lock=True, target_staff_id=target_staff_id
            )
            if assignment_result.assigned_staff_id != target_staff_id:
                raise changed()
        else:
            visitor = (
                db.query(Visitor)
                .filter(
                    Visitor.id == visitor_id,
                    Visitor.project_id == project_id,
                    Visitor.deleted_at.is_(None),
                    Visitor.access_retired_at.is_(None),
                )
                .populate_existing()
                .with_for_update()
                .first()
            )
            if visitor is None:
                raise changed()

        if binding != reception_binding(db, visitor, session_id, lock=True):
            raise changed()
        if expected_queue_entry_id is not None:
            expected_entry = lock_waiting_entry(
                db, visitor, expected_queue_entry_id
            )
            if expected_entry is None:
                raise changed()
            expected_entry.record_attempt()
        if session_scope is None:
            session = await _get_or_create_session(
                db=db,
                visitor_id=visitor_id,
                project_id=project_id,
                session_id=session_id,
                platform_id=platform_id or visitor.platform_id,
            )

        configuration = assignment_result.configuration
        if (
            configuration is not None
            and not assignment_configuration_is_current(db, configuration)
        ):
            assignment_result.assigned_staff_id = None
            assignment_result.llm_response = None
            assignment_result.candidate_scores = None
            assignment_result.llm_reasoning = (
                "Configuration changed during assignment; "
                "fallback to load balancing"
            )

        current_rule = read_assignment_rule(db, project_id, lock=True)
        await _reserve_assignment(
            db,
            project_id,
            session.id,
            assignment_result,
            current_rule,
            target_staff_id,
        )

        previous_staff_id = session.staff_id

        assigned_staff_id = assignment_result.assigned_staff_id
        candidate_staff_ids = assignment_result.candidate_staff_ids
        llm_response = assignment_result.llm_response
        llm_reasoning = assignment_result.llm_reasoning
        candidate_scores = assignment_result.candidate_scores
        model_used = assignment_result.model_used
        prompt_used = assignment_result.prompt_used

        # 5. Handle case when no staff is assigned - optionally add to waiting
        # queue
        if (
            not assigned_staff_id
            and len(candidate_staff_ids) == 0
            and add_to_queue_if_no_staff
        ):
            waiting_queue_entry, queue_position = await _add_to_waiting_queue(
                db=db,
                project_id=project_id,
                visitor_id=visitor_id,
                visitor=visitor,
                session_id=session.id,
                visitor_message=visitor_message,
                reason="No available staff",
                assignment_rule=assignment_rule,
                ai_disabled=ai_disabled,
            )

        # A seat-to-seat transfer hands reception to a person, including
        # assist/auto chats.
        if source == AssignmentSource.TRANSFER:
            ai_disabled = True
        elif (
            source == AssignmentSource.RULE
            and expected_queue_entry_id is not None
            and target_staff_id is None
            and visitor.service_mode == "assist"
            and visitor.ai_disabled
        ):
            # Keep the newer assisted-service choice on background retries.
            ai_disabled = None
        # 6. Update visitor - set AI disabled status (only if explicitly
        # provided)
        if ai_disabled is not None:
            visitor.ai_disabled = ai_disabled
            if ai_disabled:
                visitor.service_mode = "manual"
                visitor.humanization_skill_enabled = False
        if assigned_staff_id:
            # Staff assigned - set to ACTIVE
            visitor.set_status_active()
            assign_waiting_entries(db, visitor, assigned_staff_id)
        # If not assigned (queued), status was already set to QUEUED above

        # 7. Update session with assigned staff
        if assigned_staff_id:
            session.staff_id = assigned_staff_id
        session.updated_at = datetime.utcnow()

        # Keep a deleted rule out of the new history FK; hold a surviving rule
        # only during the final write, never during the external model wait.
        if assignment_rule is not None:
            assignment_rule = read_assignment_rule(
                db, project_id, rule_id=assignment_rule.id, lock=True
            )

        # 8. Create assignment history record
        assignment_history = _create_assignment_history(
            db=db,
            project_id=project_id,
            visitor_id=visitor_id,
            session_id=session.id,
            assigned_staff_id=assigned_staff_id,
            previous_staff_id=previous_staff_id,
            assigned_by_staff_id=assigned_by_staff_id,
            assignment_rule=assignment_rule,
            source=source,
            visitor_message=visitor_message,
            notes=notes,
            model_used=model_used,
            prompt_used=prompt_used,
            llm_response=llm_response,
            llm_reasoning=llm_reasoning,
            candidate_staff_ids=candidate_staff_ids,
            candidate_scores=candidate_scores,
        )

        # 9. Flush and commit to release the FOR UPDATE lock on visitor
        # This minimizes lock holding time and prevents deadlocks with
        # concurrent
        # UPDATE operations on api_visitors (e.g., from message stats updates)
        if mutation_scope is not None:
            visitor.updated_at = max(
                datetime.utcnow(),
                mutation_scope.original_version + timedelta(microseconds=1),
            )
        db.flush()
        channel_delivery_id = None
        if assigned_staff_id and session_scope is None:
            channel_delivery_id = await _add_staff_to_channel(
                db=db,
                project_id=project_id,
                visitor_id=visitor_id,
                staff_id=assigned_staff_id,
                ai_disabled=visitor.ai_disabled or False,
                send_notification=send_notification,
                assignment_history=assignment_history,
                access=mutation_scope.access if mutation_scope else None,
                permission=(
                    mutation_scope.permission if mutation_scope else None
                ),
                queue_entry_id=(
                    expected_queue_entry_id if queue_scope else None
                ),
            )
        if session_scope is not None:
            assert target_staff_id is not None
            notice_target = db.get(Staff, target_staff_id)
            if notice_target is None:
                raise changed()
            notice_from = (
                db.get(Staff, previous_staff_id) if previous_staff_id else None
            )
            record_transfer(
                db,
                session_scope,
                session,
                notice_from,
                notice_target,
                operation_id or uuid4(),
            )
        if mutation_scope is not None:
            output_scope = mutation_scope.after_commit(visitor)
            if queue_scope is not None:
                assert expected_entry is not None
                output_queue = queue_scope.completed(
                    output_scope, expected_entry, session
                )
        if session_scope is not None:
            output_session_guard = session_scope.completed_guard(
                session,
                status="open",
                staff_id=target_staff_id,
            )
        if auto_commit:
            db.commit()
            db.refresh(session)
            db.refresh(assignment_history)
            db.refresh(visitor)
            if waiting_queue_entry:
                db.refresh(waiting_queue_entry)

        if mutation_scope is not None:
            assert output_scope is not None
            output_scope.guard().check()
            if output_queue is not None:
                output_queue.guard().check()
        if output_session_guard is not None:
            output_session_guard.check()

        # Only committed assignments may change the external channel.
        if channel_delivery_id is not None and auto_commit:
            await deliver_assignment_channel(
                db=db,
                operation_id=channel_delivery_id,
            )
            if output_scope is not None:
                output_scope.guard().check()
            if output_queue is not None:
                output_queue.guard().check()
        if (
            auto_commit
            and assigned_staff_id
            and session_scope is None
            and source == AssignmentSource.TRANSFER
        ):
            await notify_visitor_profile_updated(db, visitor)

        logger.info(
            (
                f"Transferred visitor "
                f"{visitor_id}"
                f" to human service. Session: "
                f"{session.id}"
                f", Assigned staff: "
                f"{assigned_staff_id}"
                f", Source: "
                f"{source.value}"
                f", Candidates: "
                f"{len(candidate_staff_ids)}"
                f", In queue: "
                f"{waiting_queue_entry is not None}"
            )
        )

        # Determine message based on result
        if assigned_staff_id:
            message = "Transfer successful"
        elif waiting_queue_entry:
            message = f"Added to waiting queue at position {queue_position}"
        else:
            message = "Transfer successful, awaiting staff assignment"

        return TransferResult(
            success=True,
            session=session,
            assignment_history=assignment_history,
            assigned_staff_id=assigned_staff_id,
            candidate_staff_ids=candidate_staff_ids,
            waiting_queue=waiting_queue_entry,
            queue_position=queue_position,
            message=message,
            operation_guard=output_queue.guard()
            if output_queue is not None
            else output_scope.guard()
            if output_scope is not None
            else output_session_guard,
        )

    except Exception as e:
        db.rollback()
        if mutation_scope is not None or session_scope is not None:
            if isinstance(e, (TGOAPIException, HTTPException)):
                raise
            logger.error(
                "Manual visitor transfer failed",
                extra={
                    "visitor_id": str(visitor_id),
                    "error_type": type(e).__name__,
                },
            )
            raise TGOAPIException(
                "接入失败，请刷新客户状态后重试", "TRANSFER_FAILED", status_code=502
            ) from e
        logger.error(
            "Visitor transfer failed",
            extra={
                "visitor_id": str(visitor_id),
                "error_type": type(e).__name__,
            },
        )
        return TransferResult(
            success=False,
            session=None,
            assignment_history=None,
            assigned_staff_id=None,
            candidate_staff_ids=None,
            waiting_queue=None,
            queue_position=None,
            message="接入失败，请刷新客户状态后重试",
        )


async def assign_staff(
    db: Session,
    visitor_id: UUID,
    project_id: UUID,
    target_staff_id: Optional[UUID] = None,
    visitor_message: Optional[str] = None,
    assignment_rule: Optional[VisitorAssignmentRule] = None,
    strict_target: bool = False,
    lock_assignment_configuration: bool = True,
) -> StaffAssignmentResult:
    (
        "\n"
        "    Assign a staff member to handle a visitor.\n"
        "    \n"
        "    Assignment logic:\n"
        "    1. If target_staff_id specified, assign directly\n"
        "    2. Otherwise, get available staff candidates based on rules\n"
        "    3. Prioritize last serving staff if available\n"
        "    4. If candidates > 1 and LLM enabled, use LLM to select\n"
        "    5. If candidates > 1 and LLM disabled, use load balancing\n"
        "    6. If candidates = 1, assign directly\n"
        "    7. If candidates = 0, return None\n"
        "    \n"
        "    Args:\n"
        "        db: Database session\n"
        "        visitor_id: ID of the visitor\n"
        "        project_id: ID of the project\n"
        "        target_staff_id: Specific staff to assign (optional)\n"
        "        visitor_message: Message that triggered the transfer (for "
        "LLM context)\n"
        "        assignment_rule: Assignment rule for the project (optional)\n"
        "        \n"
        "    Returns:\n"
        "        StaffAssignmentResult with assigned_staff_id and candidate "
        "info\n"
        "    "
    )
    assigned_staff_id: Optional[UUID] = None
    candidate_staff_ids: List[UUID] = []
    llm_response: Optional[str] = None
    llm_reasoning: Optional[str] = None
    candidate_scores: Optional[dict[str, JsonValue]] = None
    model_used: Optional[str] = None
    prompt_used: Optional[str] = None
    configuration: AssignmentConfiguration | None = None

    # Get visitor for LLM context
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )

    if target_staff_id:
        # Direct assignment to specified staff
        staff = (
            db.query(Staff)
            .filter(
                Staff.id == target_staff_id,
                Staff.project_id == project_id,
                Staff.deleted_at.is_(None),
                Staff.account_enabled.is_(True),
                Staff.role.in_(["admin", "user"]),
            )
            .populate_existing()
            .first()
        )

        if staff:
            if (
                settings.SAAS_BILLING_ENABLED
                and not staff.is_available_for_service
            ):
                raise ValueError("Target staff account is not available")
            assigned_staff_id = target_staff_id
            candidate_staff_ids = [target_staff_id]
            logger.info(f"Direct assignment to staff {target_staff_id}")
        else:
            if settings.SAAS_BILLING_ENABLED:
                raise ValueError("Target staff account is not available")
            if strict_target:
                raise changed()
            logger.warning(
                (
                    f"Target staff "
                    f"{target_staff_id}"
                    f" not found, will try auto-assignment"
                )
            )

    # Auto-assignment if no target specified or target not found
    if not assigned_staff_id:
        # Get assignment rule if not provided
        if assignment_rule is None:
            assignment_rule = (
                db.query(VisitorAssignmentRule)
                .filter(
                    VisitorAssignmentRule.project_id == project_id,
                )
                .first()
            )

        # Get available staff candidates
        candidates = await _get_available_staff_candidates(
            db=db,
            project_id=project_id,
            assignment_rule=assignment_rule,
        )

        candidate_staff_ids = [c.id for c in candidates]

        if len(candidates) == 0:
            # No available staff
            logger.info(f"No available staff for project {project_id}")

        elif len(candidates) == 1:
            # Single candidate, assign directly
            assigned_staff_id = candidates[0].id
            logger.info(
                f"Single candidate {assigned_staff_id}, assigning directly"
            )

        else:
            # Multiple candidates - first check for last serving staff
            last_session = (
                db.query(VisitorSession)
                .join(Staff, VisitorSession.staff_id == Staff.id)
                .filter(
                    VisitorSession.visitor_id == visitor_id,
                    VisitorSession.project_id == project_id,
                    VisitorSession.staff_id.isnot(None),
                    Staff.deleted_at.is_(None),  # Ensure staff is not deleted
                )
                .order_by(VisitorSession.created_at.desc())
                .first()
            )

            if last_session and last_session.staff_id:
                last_staff_id = last_session.staff_id
                # Check if last staff is among available candidates
                for candidate in candidates:
                    if candidate.id == last_staff_id:
                        assigned_staff_id = last_staff_id
                        logger.info(
                            (
                                f"Prioritizing last serving staff "
                                f"{last_staff_id}"
                                f" for visitor "
                                f"{visitor_id}"
                            ),
                            extra={
                                "visitor_id": str(visitor_id),
                                "last_staff_id": str(last_staff_id),
                            },
                        )
                        break

            # If no last serving staff available, use LLM or load balancing
            if not assigned_staff_id:
                if (
                    assignment_rule
                    and assignment_rule.llm_assignment_enabled
                    and visitor
                ):
                    # Use LLM to select
                    logger.info(
                        (
                            f"Multiple candidates ("
                            f"{len(candidates)}"
                            f"), using LLM assignment"
                        )
                    )
                    result = await _llm_assign_staff(
                        db=db,
                        project_id=project_id,
                        visitor=visitor,
                        visitor_message=visitor_message,
                        candidates=candidates,
                        assignment_rule=assignment_rule,
                        lock_configuration=lock_assignment_configuration,
                    )
                    if result["refresh_candidates"]:
                        current_rule = read_assignment_rule(
                            db, project_id, lock=lock_assignment_configuration
                        )
                        candidates = await _get_available_staff_candidates(
                            db, project_id, current_rule
                        )
                        candidate_staff_ids = [item.id for item in candidates]
                        assigned_staff_id = await _load_balance_assign(
                            candidates
                        )
                    else:
                        assigned_staff_id = result.get("selected_staff_id")
                    llm_response = result.get("llm_response")
                    llm_reasoning = result.get("reasoning")
                    candidate_scores = result.get("scores")
                    model_used = result.get("model_used")
                    prompt_used = result.get("prompt_used")
                    configuration = result["configuration"]
                else:
                    # Use load balancing (select staff with least active chats)
                    logger.info(
                        (
                            f"Multiple candidates ("
                            f"{len(candidates)}"
                            f"), using load balancing"
                        )
                    )
                    assigned_staff_id = await _load_balance_assign(candidates)

    return StaffAssignmentResult(
        assigned_staff_id=assigned_staff_id,
        candidate_staff_ids=candidate_staff_ids,
        llm_response=llm_response,
        llm_reasoning=llm_reasoning,
        candidate_scores=candidate_scores,
        model_used=model_used,
        prompt_used=prompt_used,
        configuration=configuration,
    )


async def _reserve_assignment(
    db: Session,
    project_id: UUID,
    session_id: UUID | None,
    result: StaffAssignmentResult,
    rule: VisitorAssignmentRule | None,
    target_staff_id: UUID | None,
) -> None:
    """Refresh after model selection and reserve without another model call."""
    capacity = rule.max_concurrent_chats if rule is not None else None
    if target_staff_id is not None:
        if (
            result.assigned_staff_id != target_staff_id
            or not reserve_staff_seat(
                db, project_id, target_staff_id, session_id, capacity
            )
        ):
            raise changed()
        return

    candidates = await _get_available_staff_candidates(db, project_id, rule)
    preferred = result.assigned_staff_id
    selected: UUID | None = None
    while candidates:
        identifiers = [item.id for item in candidates]
        choice = (
            preferred
            if preferred in identifiers
            else (await _load_balance_assign(candidates))
        )
        assert choice is not None
        if reserve_staff_seat(db, project_id, choice, session_id, capacity):
            selected = choice
            break
        candidates = [item for item in candidates if item.id != choice]
    result.assigned_staff_id = selected
    result.candidate_staff_ids = [item.id for item in candidates]
    if selected != preferred:
        result.llm_response = None
        result.candidate_scores = None
        if preferred is not None or result.llm_reasoning is None:
            result.llm_reasoning = (
                "Selected staff unavailable; "
                "fallback to current staff capacity"
            )


async def _add_to_waiting_queue(
    db: Session,
    project_id: UUID,
    visitor_id: UUID,
    visitor: Visitor,
    session_id: UUID,
    visitor_message: Optional[str],
    reason: str,
    assignment_rule: Optional[VisitorAssignmentRule],
    ai_disabled: Optional[bool],
) -> tuple[VisitorWaitingQueue, int]:
    (
        "\n"
        "    Add visitor to the waiting queue.\n"
        "    \n"
        "    Args:\n"
        "        db: Database session\n"
        "        project_id: Project ID\n"
        "        visitor_id: Visitor ID\n"
        "        visitor: Visitor object\n"
        "        session_id: Session ID\n"
        "        visitor_message: Message that triggered the transfer\n"
        "        reason: Reason for entering queue\n"
        "        assignment_rule: Assignment rule for timeout config\n"
        "        ai_disabled: AI disabled flag\n"
        "        \n"
        "    Returns:\n"
        "        Tuple of (waiting_queue_entry, queue_position)\n"
        "    "
    )
    # Check if already in waiting queue
    existing_queue = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.visitor_id == visitor_id,
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .first()
    )

    if existing_queue:
        logger.info(
            (
                f"Visitor "
                f"{visitor_id}"
                f" already in waiting queue at position "
                f"{existing_queue.position}"
            )
        )
        return existing_queue, existing_queue.position

    # Calculate queue position
    current_queue_count = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .count()
    )
    queue_position = current_queue_count + 1

    # Calculate expiration time
    timeout_minutes = settings.QUEUE_DEFAULT_TIMEOUT_MINUTES
    if assignment_rule and assignment_rule.queue_wait_timeout_minutes:
        timeout_minutes = assignment_rule.queue_wait_timeout_minutes
    expired_at = datetime.utcnow() + timedelta(minutes=timeout_minutes)

    # Create queue entry
    waiting_queue_entry = VisitorWaitingQueue(
        project_id=project_id,
        visitor_id=visitor_id,
        session_id=session_id,
        source=QueueSource.NO_STAFF.value,
        position=queue_position,
        priority=0,
        status=WaitingStatus.WAITING.value,
        visitor_message=visitor_message,
        reason=reason,
        expired_at=expired_at,
        ai_disabled=ai_disabled,
    )
    db.add(waiting_queue_entry)

    # Update visitor status to QUEUED
    visitor.set_status_queued()

    logger.info(
        (
            f"Added visitor "
            f"{visitor_id}"
            f" to waiting queue at position "
            f"{queue_position}"
        ),
        extra={
            "visitor_id": str(visitor_id),
            "queue_position": queue_position,
            "expired_at": str(expired_at),
            "timeout_minutes": timeout_minutes,
        },
    )

    schedule_queue_update(db, project_id, "entered")

    return waiting_queue_entry, queue_position


async def _add_staff_to_channel(
    db: Session,
    project_id: UUID,
    visitor_id: UUID,
    staff_id: UUID,
    ai_disabled: bool = False,
    send_notification: bool = True,
    operation_guard: OperationGuard | None = None,
    *,
    assignment_history: VisitorAssignmentHistory | None = None,
    access: StaffDataAccess | None = None,
    permission: str | None = None,
    queue_entry_id: UUID | None = None,
) -> UUID | None:
    if operation_guard is not None:
        operation_guard.check()
    if (
        assignment_history is None
        or assignment_history.project_id != project_id
        or assignment_history.visitor_id != visitor_id
        or assignment_history.assigned_staff_id != staff_id
    ):
        raise ValueError("A matching assignment is required for channel work")
    return stage_assignment_channel(
        db,
        assignment_history,
        send_notification=send_notification,
        ai_disabled=ai_disabled,
        access=access,
        permission=permission,
        queue_entry_id=queue_entry_id,
    )


def _create_assignment_history(
    db: Session,
    project_id: UUID,
    visitor_id: UUID,
    session_id: UUID,
    assigned_staff_id: Optional[UUID],
    previous_staff_id: Optional[UUID],
    assigned_by_staff_id: Optional[UUID],
    assignment_rule: Optional[VisitorAssignmentRule],
    source: AssignmentSource,
    visitor_message: Optional[str],
    notes: Optional[str],
    model_used: Optional[str],
    prompt_used: Optional[str],
    llm_response: Optional[str],
    llm_reasoning: Optional[str],
    candidate_staff_ids: List[UUID],
    candidate_scores: Optional[dict[str, JsonValue]],
) -> VisitorAssignmentHistory:
    (
        "\n"
        "    Create assignment history record.\n"
        "    \n"
        "    Returns:\n"
        "        VisitorAssignmentHistory object (not yet committed)\n"
        "    "
    )
    assignment_history = VisitorAssignmentHistory(
        project_id=project_id,
        visitor_id=visitor_id,
        session_id=session_id,
        assigned_staff_id=assigned_staff_id,
        previous_staff_id=previous_staff_id,
        assigned_by_staff_id=assigned_by_staff_id,
        assignment_rule_id=assignment_rule.id if assignment_rule else None,
        source=source.value,
        visitor_message=visitor_message,
        notes=notes,
        model_used=model_used,
        prompt_used=prompt_used,
        llm_response=llm_response,
        reasoning=llm_reasoning,
        candidate_staff_ids=[str(sid) for sid in candidate_staff_ids]
        if candidate_staff_ids
        else None,
        candidate_scores=candidate_scores,
    )
    db.add(assignment_history)
    return assignment_history


async def _get_or_create_session(
    db: Session,
    visitor_id: UUID,
    project_id: UUID,
    session_id: Optional[UUID] = None,
    platform_id: Optional[UUID] = None,
) -> VisitorSession:
    """
    Get existing session or create a new one.
    """
    # If session_id provided, try to get it
    if session_id:
        session = (
            db.query(VisitorSession)
            .filter(
                VisitorSession.id == session_id,
                VisitorSession.project_id == project_id,
                VisitorSession.visitor_id == visitor_id,
                VisitorSession.status == SessionStatus.OPEN.value,
            )
            .populate_existing()
            .first()
        )
        if session is None:
            raise changed()
        return session

    # Try to find an open session for this visitor
    session = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.visitor_id == visitor_id,
            VisitorSession.project_id == project_id,
            VisitorSession.status == SessionStatus.OPEN.value,
        )
        .order_by(VisitorSession.created_at.desc())
        .first()
    )

    if session:
        return session

    # Create new session
    session = VisitorSession(
        project_id=project_id,
        visitor_id=visitor_id,
        platform_id=platform_id,
        status=SessionStatus.OPEN.value,
    )
    db.add(session)
    db.flush()

    logger.info(f"Created new session {session.id} for visitor {visitor_id}")
    return session


def is_within_service_hours(
    assignment_rule: Optional[VisitorAssignmentRule],
) -> bool:
    (
        "\n"
        "    Check if current time is within configured service hours.\n"
        "    \n"
        "    The service hours are evaluated in the configured timezone "
        "(default: Asia/Shanghai).\n"
        "    \n"
        "    Returns True if:\n"
        "    - No assignment rule configured\n"
        "    - Service hours not configured (all fields are None)\n"
        "    - Current time is within service hours (in the configured "
        "timezone)\n"
        "    "
    )
    if not assignment_rule:
        return True

    # Get the configured timezone, default to Asia/Shanghai
    tz_name = assignment_rule.timezone or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except Exception as e:
        logger.warning(
            f"Invalid timezone '{tz_name}': {e}, using Asia/Shanghai"
        )
        tz = ZoneInfo("Asia/Shanghai")

    # Get current time in the configured timezone
    now = datetime.now(tz)

    # Check weekday (1=Monday, 7=Sunday in our config; Python: 0=Monday,
    # 6=Sunday)
    if assignment_rule.service_weekdays:
        # Convert Python weekday (0-6) to our format (1-7)
        current_weekday = now.weekday() + 1
        if current_weekday not in assignment_rule.service_weekdays:
            logger.debug(
                (
                    f"Current weekday "
                    f"{current_weekday}"
                    f" (tz="
                    f"{tz_name}"
                    f") not in service weekdays "
                    f"{assignment_rule.service_weekdays}"
                )
            )
            return False

    # Check time range
    if assignment_rule.service_start_time and assignment_rule.service_end_time:
        try:
            start_parts = assignment_rule.service_start_time.split(":")
            end_parts = assignment_rule.service_end_time.split(":")

            start_time = time(int(start_parts[0]), int(start_parts[1]))
            end_time = time(int(end_parts[0]), int(end_parts[1]))
            current_time = now.time()

            # Handle normal case (e.g., 09:00 - 18:00)
            if start_time <= end_time:
                if not (start_time <= current_time <= end_time):
                    logger.debug(
                        (
                            f"Current time "
                            f"{current_time}"
                            f" (tz="
                            f"{tz_name}"
                            f") not in service hours "
                            f"{start_time}"
                            f"-"
                            f"{end_time}"
                        )
                    )
                    return False
            else:
                # Handle overnight case (e.g., 22:00 - 06:00)
                if not (
                    current_time >= start_time or current_time <= end_time
                ):
                    logger.debug(
                        (
                            f"Current time "
                            f"{current_time}"
                            f" (tz="
                            f"{tz_name}"
                            f") not in overnight service hours "
                            f"{start_time}"
                            f"-"
                            f"{end_time}"
                        )
                    )
                    return False
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid service time format: {e}")
            # If time format is invalid, don't block assignment
            return True

    return True


async def _get_available_staff_candidates(
    db: Session,
    project_id: UUID,
    assignment_rule: Optional[VisitorAssignmentRule] = None,
) -> List[StaffCandidate]:
    (
        "\n"
        "    Get available staff candidates for assignment.\n"
        "    \n"
        "    Filters:\n"
        "    - Not deleted\n"
        "    - Role is user only (not admin or agent)\n"
        "    - Within service hours (if configured)\n"
        "    - Within max_concurrent_chats limit (if configured)\n"
        "    "
    )
    # Check if within service hours
    if not is_within_service_hours(assignment_rule):
        logger.info(f"Outside service hours for project {project_id}")
        return []

    # Get max concurrent chats from rule
    max_concurrent = None
    if assignment_rule and assignment_rule.max_concurrent_chats:
        max_concurrent = assignment_rule.max_concurrent_chats

    # Query available staff (only user role, not admin or agent, active and not
    # paused)
    staff_query = db.query(Staff).filter(
        Staff.project_id == project_id,
        Staff.deleted_at.is_(None),
        Staff.is_active
        == True,  # noqa: E712 - SQLAlchemy requires == for boolean
        Staff.account_enabled.is_(True),
        Staff.role.in_(["admin", "user"]),
        Staff.service_paused
        == False,  # noqa: E712 - SQLAlchemy requires == for boolean
    )

    available_staff = staff_query.populate_existing().all()

    if not available_staff:
        return []

    # Get current active chat count for each staff
    candidates = []
    for staff in available_staff:
        # Count active sessions for this staff
        active_session_count = (
            db.query(func.count(VisitorSession.id))
            .filter(
                VisitorSession.staff_id == staff.id,
                VisitorSession.status == SessionStatus.OPEN.value,
            )
            .scalar()
            or 0
        )

        # Skip if at max capacity
        if max_concurrent and active_session_count >= max_concurrent:
            logger.debug(
                (
                    f"Staff "
                    f"{staff.id}"
                    f" at max capacity ("
                    f"{active_session_count}"
                    f"/"
                    f"{max_concurrent}"
                    f")"
                )
            )
            continue

        candidates.append(
            StaffCandidate(
                id=staff.id,
                name=staff.name,
                nickname=staff.nickname,
                description=staff.description,
                status=staff.status,
                current_chat_count=active_session_count,
            )
        )

    return candidates


async def _load_balance_assign(
    candidates: List[StaffCandidate],
) -> Optional[UUID]:
    """
    Select staff with lowest current chat count (load balancing).
    Prioritizes online staff over offline/busy staff.
    """
    if not candidates:
        return None

    # Sort by status priority (online > busy > offline) and then by current
    # chat count ascending
    # status_priority: online(0) < busy(1) < offline(2)
    def sort_key(c: StaffCandidate) -> tuple:
        status_priority = {"online": 0, "busy": 1, "offline": 2}
        return (status_priority.get(c.status, 2), c.current_chat_count)

    sorted_candidates = sorted(candidates, key=sort_key)
    return sorted_candidates[0].id


async def _llm_assign_staff(
    db: Session,
    project_id: UUID,
    visitor: Visitor,
    visitor_message: Optional[str],
    candidates: List[StaffCandidate],
    assignment_rule: VisitorAssignmentRule,
    *,
    lock_configuration: bool = True,
) -> LLMStaffAssignmentResult:
    """Select a listed staff member or use a safe load-balancing fallback."""
    result: LLMStaffAssignmentResult = {
        "configuration": None,
        "refresh_candidates": False,
        "selected_staff_id": None,
        "llm_response": None,
        "reasoning": None,
        "scores": None,
        "model_used": None,
        "prompt_used": None,
    }
    configuration = capture_assignment_configuration(
        db, project_id, assignment_rule
    )
    if configuration is None:
        result["refresh_candidates"] = True
        result["selected_staff_id"] = await _load_balance_assign(candidates)
        result[
            "reasoning"
        ] = "AI provider or model unavailable; fallback to load balancing"
        return result

    # Build staff info for prompt
    staff_info_list = []
    for i, c in enumerate(candidates, 1):
        name = c.name or c.nickname or f"Staff_{c.id}"
        desc = c.description or "No description available"
        staff_info_list.append(
            f"{i}. ID: {c.id}\n   Name: {name}\n"
            f"   Description: {desc}\n"
            f"   Current chats: {c.current_chat_count}"
        )

    staff_info = "\n".join(staff_info_list)

    # Build visitor info
    visitor_name = visitor.name or visitor.nickname or "Unknown"
    visitor_info = f"Name: {visitor_name}"
    if visitor_message:
        visitor_info += f"\nMessage: {visitor_message}"

    # Get the prompt
    system_prompt = configuration.system_prompt

    # Build the user message
    user_message = f"""请根据以下信息，选择最合适的客服人员来处理此访客。

## 访客信息
{visitor_info}

## 可用客服列表
{staff_info}

请返回JSON格式的结果：
{{
  "selected_staff_id": "选中的客服ID",
  "reasoning": "选择理由"
}}

只返回JSON，不要其他内容。"""

    result["prompt_used"] = f"System: {system_prompt}\n\nUser: {user_message}"
    result["model_used"] = configuration.selection.model

    had_error = False
    response: object = None
    try:
        from app.services.ai_client import ai_client

        response = await ai_client.chat_completions(
            project_id=str(project_id),
            provider_id=str(configuration.selection.provider_id),
            model=configuration.selection.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=500,
        )
    except Exception as error:
        logger.warning(
            "LLM assignment unavailable; using load balancing",
            extra={"error_type": type(error).__name__},
        )
        had_error = True

    if not assignment_configuration_is_current(
        db, configuration, lock=lock_configuration
    ):
        result["refresh_candidates"] = True
        result["selected_staff_id"] = await _load_balance_assign(candidates)
        result["reasoning"] = (
            "Configuration changed during assignment; "
            "fallback to load balancing"
        )
        return result

    result["configuration"] = configuration
    if had_error:
        result["selected_staff_id"] = await _load_balance_assign(candidates)
        result[
            "reasoning"
        ] = "LLM assignment failed; fallback to load balancing"
        return result

    try:
        content = assignment_response_content(response)
        result["llm_response"] = content
        selected_id, reasoning = assignment_selection(
            content, {candidate.id for candidate in candidates}
        )
    except ValueError:
        logger.warning("Invalid LLM assignment; using load balancing")
        result["selected_staff_id"] = await _load_balance_assign(candidates)
        result[
            "reasoning"
        ] = "Invalid LLM response; fallback to load balancing"
        return result

    result["selected_staff_id"] = selected_id
    result["reasoning"] = reasoning
    logger.info("LLM selected staff", extra={"staff_id": str(selected_id)})
    return result


async def reassign_to_staff(
    db: Session,
    visitor_id: UUID,
    project_id: UUID,
    new_staff_id: UUID,
    assigned_by_staff_id: Optional[UUID] = None,
    session_id: Optional[UUID] = None,
    notes: Optional[str] = None,
) -> TransferResult:
    """
    Reassign a visitor to a different staff member.
    """
    return await transfer_to_staff(
        db=db,
        visitor_id=visitor_id,
        project_id=project_id,
        source=AssignmentSource.TRANSFER,
        target_staff_id=new_staff_id,
        assigned_by_staff_id=assigned_by_staff_id,
        session_id=session_id,
        notes=notes,
    )


async def assign_from_waiting_queue(
    db: Session,
    staff_id: UUID,
    project_id: UUID,
    queue_entry_id: Optional[UUID] = None,
) -> Optional[TransferResult]:
    (
        "\n"
        "    Assign the next visitor from waiting queue to a staff member.\n"
        "    \n"
        "    Args:\n"
        "        db: Database session\n"
        "        staff_id: Staff member to assign visitor to\n"
        "        project_id: Project ID\n"
        "        queue_entry_id: Specific queue entry to assign (optional, "
        "defaults to next in line)\n"
        "        \n"
        "    Returns:\n"
        "        TransferResult if successful, None if queue is empty\n"
        "    "
    )
    # Validate staff exists
    staff = (
        db.query(Staff)
        .filter(
            Staff.id == staff_id,
            Staff.project_id == project_id,
            Staff.deleted_at.is_(None),
        )
        .first()
    )

    if not staff:
        logger.warning(f"Staff {staff_id} not found for queue assignment")
        return None

    # Get the queue entry
    if queue_entry_id:
        queue_entry = (
            db.query(VisitorWaitingQueue)
            .filter(
                VisitorWaitingQueue.id == queue_entry_id,
                VisitorWaitingQueue.project_id == project_id,
                VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
            )
            .first()
        )
    else:
        # Get the next visitor in queue (ordered by priority desc, position
        # asc)
        queue_entry = (
            db.query(VisitorWaitingQueue)
            .filter(
                VisitorWaitingQueue.project_id == project_id,
                VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
            )
            .order_by(
                VisitorWaitingQueue.priority.desc(),
                VisitorWaitingQueue.position.asc(),
            )
            .first()
        )

    if not queue_entry:
        logger.info(f"No visitors in waiting queue for project {project_id}")
        return None

    logger.info(
        (
            f"Assigning visitor "
            f"{queue_entry.visitor_id}"
            f" from queue to staff "
            f"{staff_id}"
        )
    )

    # Transfer the visitor to the staff
    result = await transfer_to_staff(
        db=db,
        visitor_id=queue_entry.visitor_id,
        project_id=project_id,
        source=AssignmentSource.RULE,
        target_staff_id=staff_id,
        session_id=queue_entry.session_id,
        visitor_message=queue_entry.visitor_message,
        notes=(
            f"Assigned from waiting queue (position: "
            f"{queue_entry.position}"
            f")"
        ),
        ai_disabled=queue_entry.ai_disabled,
        expected_queue_entry_id=queue_entry.id,
    )

    return result


def get_waiting_queue_count(db: Session, project_id: UUID) -> int:
    """Get the number of visitors waiting in queue for a project."""
    return (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .count()
    )


def get_visitor_queue_position(
    db: Session, visitor_id: UUID, project_id: UUID
) -> Optional[int]:
    (
        "Get a visitor's position in the waiting queue, or None if not in "
        "queue."
    )
    queue_entry = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.visitor_id == visitor_id,
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .first()
    )

    if not queue_entry:
        return None

    # Count how many visitors are ahead (higher priority or lower position)
    position = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
            (
                (VisitorWaitingQueue.priority > queue_entry.priority)
                | (
                    (VisitorWaitingQueue.priority == queue_entry.priority)
                    & (VisitorWaitingQueue.position < queue_entry.position)
                )
            ),
        )
        .count()
        + 1
    )

    return position


async def cancel_visitor_from_queue(
    db: Session,
    visitor_id: UUID,
    project_id: UUID,
) -> bool:
    (
        "\n"
        "    Cancel a visitor's waiting queue entry.\n"
        "    \n"
        "    Returns True if cancelled, False if not in queue.\n"
        "    "
    )
    queue_entry = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.visitor_id == visitor_id,
            VisitorWaitingQueue.project_id == project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .first()
    )

    if not queue_entry:
        return False

    if not cancel_entry(db, queue_entry.id, project_id):
        return False
    db.commit()

    logger.info(f"Cancelled visitor {visitor_id} from waiting queue")
    return True
