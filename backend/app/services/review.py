"""The human review queue.

The rule this module exists to enforce: **a human resolution never overwrites a
machine decision.** The two are stored in different tables, and the decision
table rejects writes after insert. When an analyst approves a transaction the
engine blocked, both facts survive - which is the only arrangement in which
"how often do analysts overturn us?" is an answerable question.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.models import AnalystDecision, AuditLog, ReviewCase, RiskDecision, Transaction
from app.models.enums import (
    ActorType,
    AnalystDecisionType,
    DecisionAction,
    ReviewCaseStatus,
    ReviewResolution,
)
from policy.engine import PolicyResult

logger = logging.getLogger(__name__)


class ReviewCaseError(RuntimeError):
    """The requested review-queue operation is not valid."""


#: How a resolution is recorded on the analyst-decision ledger. ``REJECTED``
#: means the analyst stopped the payment, so it lands as ``block``.
_RESOLUTION_TO_DECISION: dict[ReviewResolution, AnalystDecisionType] = {
    ReviewResolution.APPROVED: AnalystDecisionType.APPROVE,
    ReviewResolution.REJECTED: AnalystDecisionType.BLOCK,
    ReviewResolution.ESCALATED: AnalystDecisionType.ESCALATED,
}

_RESOLUTION_TO_STATUS: dict[ReviewResolution, ReviewCaseStatus] = {
    ReviewResolution.APPROVED: ReviewCaseStatus.RESOLVED,
    ReviewResolution.REJECTED: ReviewCaseStatus.RESOLVED,
    #: Escalation is not a resolution - the case stays live for someone senior.
    ReviewResolution.ESCALATED: ReviewCaseStatus.ESCALATED,
}

#: Resolutions after which no further resolution is accepted.
_TERMINAL_STATUSES = frozenset({ReviewCaseStatus.RESOLVED})


def open_case_for_decision(
    session: Session,
    transaction: Transaction,
    decision: RiskDecision,
    result: PolicyResult,
) -> ReviewCase:
    """Queue a transaction for a human, linked to the decision that sent it there.

    ``review_cases.transaction_id`` is unique, so a transaction has at most one
    live case. A re-decision that again requires review re-points the existing
    case at the newer decision and reopens it rather than creating a duplicate;
    the decision history keeps every evaluation, so nothing is lost by holding
    one queue entry per transaction.
    """
    case = session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    reason = f"{result.action}: {', '.join(result.deciding_rules) or 'policy default'}"[:255]

    if case is None:
        case = ReviewCase(transaction_id=transaction.id)
        session.add(case)
    elif case.status in _TERMINAL_STATUSES:
        # A previously settled case is being reopened by a new decision. The old
        # resolution is cleared from the queue entry, but the AnalystDecision
        # rows recording it remain, so the history stays intact.
        case.resolution = None
        case.resolution_reason = None
        case.resolved_at = None

    case.status = ReviewCaseStatus.OPEN
    case.reason = reason
    case.risk_decision_id = decision.id
    session.flush()

    session.add(
        AuditLog(
            transaction_id=transaction.id,
            actor_type=ActorType.SYSTEM,
            actor_id="risk-decision-engine",
            event_type="review.case_opened",
            event_data={
                "review_case_id": case.id,
                "decision_id": decision.public_id,
                "decision": str(result.action),
                "reason_codes": list(result.reason_codes),
            },
        )
    )
    session.flush()
    return case


def _queue_query() -> Select[tuple[ReviewCase]]:
    return select(ReviewCase).options(
        selectinload(ReviewCase.transaction),
        selectinload(ReviewCase.risk_decision),
    )


def queue_statement(
    *,
    status: ReviewCaseStatus | None = None,
    resolution: ReviewResolution | None = None,
    decision: DecisionAction | None = None,
    transaction_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> Select[tuple[ReviewCase]]:
    """The filtered queue query, ready for :func:`app.services.pagination.paginate`.

    Ordered newest-first with the primary key as tiebreak, so paging stays
    stable when several cases share a creation timestamp.
    """
    query = _queue_query()

    if transaction_id is not None:
        query = query.join(ReviewCase.transaction).where(
            Transaction.transaction_id == transaction_id
        )
    if decision is not None:
        query = query.join(ReviewCase.risk_decision).where(RiskDecision.action == decision)
    if status is not None:
        query = query.where(ReviewCase.status == status)
    if resolution is not None:
        query = query.where(ReviewCase.resolution == resolution)
    if created_after is not None:
        query = query.where(ReviewCase.created_at >= created_after)
    if created_before is not None:
        query = query.where(ReviewCase.created_at <= created_before)

    return query.order_by(ReviewCase.created_at.desc(), ReviewCase.id.desc())


def load_case(session: Session, case_id: int) -> ReviewCase | None:
    return session.scalar(_queue_query().where(ReviewCase.id == case_id))


def resolve_case(
    session: Session,
    case: ReviewCase,
    resolution: ReviewResolution,
    *,
    analyst_id: int | None = None,
    reason: str | None = None,
) -> AnalystDecision:
    """Record an analyst's resolution alongside - never over - the machine decision.

    The linked :class:`RiskDecision` is not touched. Attempting to touch it
    would raise: the decision table is append-only and the guard runs on flush.
    """
    if case.status in _TERMINAL_STATUSES:
        raise ReviewCaseError(
            f"review case {case.id} is already resolved as {case.resolution}; "
            "resolutions are recorded once"
        )

    now = datetime.now(UTC)
    case.resolution = resolution
    case.resolution_reason = reason
    case.status = _RESOLUTION_TO_STATUS[resolution]
    case.resolved_at = now if resolution is not ReviewResolution.ESCALATED else None
    if analyst_id is not None:
        case.assigned_to = analyst_id

    analyst_decision = AnalystDecision(
        review_case_id=case.id,
        analyst_id=analyst_id,
        decision=_RESOLUTION_TO_DECISION[resolution],
        reason=reason,
    )
    session.add(analyst_decision)
    session.flush()

    machine_action = str(case.risk_decision.action) if case.risk_decision else None
    session.add(
        AuditLog(
            transaction_id=case.transaction_id,
            actor_type=ActorType.ANALYST,
            actor_id=str(analyst_id) if analyst_id is not None else "unassigned",
            event_type="review.resolved",
            event_data={
                "review_case_id": case.id,
                "resolution": str(resolution),
                "decision_id": case.risk_decision.public_id if case.risk_decision else None,
                "machine_decision": machine_action,
                # Recorded explicitly so disagreement can be counted in SQL
                # rather than inferred later from two joined tables.
                "overrides_machine_decision": is_override(machine_action, resolution),
            },
        )
    )
    session.flush()

    logger.info(
        "Review case %s resolved as %s (machine decision %s)",
        case.id,
        resolution,
        machine_action,
    )
    return analyst_decision


def is_override(machine_action: str | None, resolution: ReviewResolution) -> bool:
    """Whether the analyst *contradicted* what the engine decided.

    The subtlety that matters: a REVIEW decision is the engine declining to
    decide and asking for a human. It makes no claim about approve or block, so
    nothing the analyst then concludes can contradict it. Counting those as
    overrides would score every routed case as a disagreement and put every
    review-producing rule at a 100% override rate - which says nothing about the
    rule and would send an operator chasing a number that only measures how
    often the policy asked for help.

    An override is therefore only recorded where the engine did take a position:

    ==============  ==========  ========
    Engine          Analyst     Override
    ==============  ==========  ========
    APPROVE         REJECTED    yes
    BLOCK           APPROVED    yes
    STEP_UP         REJECTED    yes
    STEP_UP         APPROVED    no
    REVIEW          anything    no
    anything        ESCALATED   no
    ==============  ==========  ========

    Escalation settles nothing, so it is never an override. Public so the API
    layer, the audit entry and the monitoring metrics all use one definition
    rather than three that can drift apart.
    """
    if machine_action is None or resolution is ReviewResolution.ESCALATED:
        return False
    if machine_action == str(DecisionAction.REVIEW):
        return False
    if resolution is ReviewResolution.APPROVED:
        # The analyst released a payment the engine had stopped.
        return machine_action == str(DecisionAction.BLOCK)
    # REJECTED: the analyst stopped a payment the engine would have let through,
    # with or without step-up friction.
    return machine_action in (str(DecisionAction.APPROVE), str(DecisionAction.STEP_UP))


def case_decisions(session: Session, case: ReviewCase) -> Sequence[AnalystDecision]:
    """Every analyst decision recorded against a case, oldest first."""
    return list(
        session.scalars(
            select(AnalystDecision)
            .where(AnalystDecision.review_case_id == case.id)
            .order_by(AnalystDecision.created_at, AnalystDecision.id)
        )
    )
