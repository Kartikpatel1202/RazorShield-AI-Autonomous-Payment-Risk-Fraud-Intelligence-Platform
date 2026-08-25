"""Recording and aggregating analyst feedback.

Feedback is the only ground truth this system has. Everything in
:mod:`app.services.monitoring` - precision, recall, override rates,
recommendations - rests on the labels written here, so this module is strict
about what it accepts and honest about what it does not know.

Three rules shape it:

* **Feedback never touches a decision.** It is written to its own table,
  referencing the decision by id. The append-only guard would raise if this
  tried otherwise.
* **Only closed vocabularies.** Outcome and reason are enums, and the pair is
  validated against :data:`FEEDBACK_REASONS_BY_OUTCOME`, so "LEGITIMATE because
  COORDINATED_ACTIVITY" cannot enter the data and skew a breakdown someone will
  later read as fact.
* **Absent is not negative.** A transaction with no feedback is *unlabelled*,
  never a legitimate example. Counting the 19,589 unreviewed transactions as
  negatives would manufacture a 99.9% accuracy that means nothing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.metrics import feedback_total
from app.core.observability import LifecycleEvent, log_lifecycle
from app.models import AnalystFeedback, ReviewCase, RiskDecision, Transaction
from app.models.enums import (
    FEEDBACK_REASONS_BY_OUTCOME,
    DecisionAction,
    FeedbackOutcome,
    FeedbackReason,
)

logger = logging.getLogger(__name__)


class FeedbackValidationError(ValueError):
    """The submitted feedback is not internally consistent."""


def validate_pair(outcome: FeedbackOutcome, reason: FeedbackReason) -> None:
    """Reject a reason that does not belong with its outcome.

    Enum membership alone is not enough. Both values can be individually valid
    and still describe something incoherent, and a reason-code chart built from
    incoherent rows is worse than no chart.
    """
    allowed = FEEDBACK_REASONS_BY_OUTCOME[outcome]
    if reason not in allowed:
        permitted = ", ".join(sorted(str(item) for item in allowed))
        raise FeedbackValidationError(
            f"reason '{reason}' is not valid for outcome '{outcome}'. Permitted: {permitted}"
        )


def _public_id() -> str:
    return f"FBK-{uuid.uuid4().hex[:16]}"


def record_feedback(
    session: Session,
    *,
    transaction: Transaction,
    outcome: FeedbackOutcome,
    reason: FeedbackReason,
    notes: str | None = None,
    analyst_id: int | None = None,
    review_case: ReviewCase | None = None,
    decision: RiskDecision | None = None,
) -> AnalystFeedback:
    """Append one analyst conclusion.

    The decision is resolved from the review case when not supplied, so feedback
    entered from the review queue is automatically tied to the decision that
    opened it - the link every downstream metric joins on.
    """
    validate_pair(outcome, reason)

    linked = decision
    if linked is None and review_case is not None and review_case.risk_decision_id is not None:
        linked = session.get(RiskDecision, review_case.risk_decision_id)
    if linked is None:
        linked = session.scalar(
            select(RiskDecision)
            .where(RiskDecision.transaction_id == transaction.id)
            .order_by(RiskDecision.decided_at.desc(), RiskDecision.id.desc())
            .limit(1)
        )

    row = AnalystFeedback(
        public_id=_public_id(),
        transaction_id=transaction.id,
        risk_decision_id=linked.id if linked else None,
        review_case_id=review_case.id if review_case else None,
        analyst_id=analyst_id,
        outcome=outcome,
        reason_code=reason,
        notes=notes,
    )
    session.add(row)
    session.flush()

    feedback_total.labels(label=str(outcome)).inc()
    log_lifecycle(
        LifecycleEvent.FEEDBACK_CREATED,
        transaction_id=transaction.transaction_id,
        decision_id=linked.public_id if linked else None,
        feedback_id=row.public_id,
        outcome=str(outcome),
        reason_code=str(reason),
        # The analyst's own id, so the audit trail names a person. `notes` is
        # deliberately absent: it is free text an analyst typed and has no place
        # in a log stream that ships off the host.
        analyst_id=analyst_id,
        review_case_id=review_case.id if review_case else None,
    )
    return row


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------
def statement(
    *,
    outcome: FeedbackOutcome | None = None,
    reason: FeedbackReason | None = None,
    transaction_id: str | None = None,
    decision_id: str | None = None,
    analyst_id: int | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> Select[Any]:
    """The filtered feedback query, newest first.

    Joined to ``transactions`` and ``risk_decisions`` so each row carries its
    references without a lookup per row.
    """
    query = (
        select(
            AnalystFeedback.id,
            AnalystFeedback.public_id,
            AnalystFeedback.outcome,
            AnalystFeedback.reason_code,
            AnalystFeedback.notes,
            AnalystFeedback.analyst_id,
            AnalystFeedback.review_case_id,
            AnalystFeedback.created_at,
            Transaction.transaction_id,
            RiskDecision.public_id.label("decision_public_id"),
            RiskDecision.action.label("machine_decision"),
            RiskDecision.policy_version,
        )
        .select_from(AnalystFeedback)
        .join(Transaction, Transaction.id == AnalystFeedback.transaction_id)
        .join(RiskDecision, RiskDecision.id == AnalystFeedback.risk_decision_id, isouter=True)
    )

    if outcome is not None:
        query = query.where(AnalystFeedback.outcome == outcome)
    if reason is not None:
        query = query.where(AnalystFeedback.reason_code == reason)
    if transaction_id is not None:
        query = query.where(Transaction.transaction_id == transaction_id)
    if decision_id is not None:
        query = query.where(RiskDecision.public_id == decision_id)
    if analyst_id is not None:
        query = query.where(AnalystFeedback.analyst_id == analyst_id)
    if created_after is not None:
        query = query.where(AnalystFeedback.created_at >= created_after)
    if created_before is not None:
        query = query.where(AnalystFeedback.created_at <= created_before)

    return query.order_by(AnalystFeedback.created_at.desc(), AnalystFeedback.id.desc())


def page(
    session: Session, query: Select[Any], page_number: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    """One page of feedback plus the total matching count."""
    total = int(
        session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    )
    rows = (
        session.execute(query.offset((page_number - 1) * page_size).limit(page_size))
        .mappings()
        .all()
    )
    return [_to_item(row) for row in rows], total


def _to_item(row: Any) -> dict[str, Any]:
    machine = row["machine_decision"]
    return {
        "feedback_id": row["public_id"],
        "transaction_id": row["transaction_id"],
        "decision_id": row["decision_public_id"],
        "review_case_id": row["review_case_id"],
        "analyst_id": row["analyst_id"],
        "outcome": str(row["outcome"]),
        "reason_code": str(row["reason_code"]),
        "notes": row["notes"],
        "machine_decision": str(machine).upper() if machine is not None else None,
        "policy_version": row["policy_version"],
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------
# Summary and confusion matrix
# --------------------------------------------------------------------------
def summary(session: Session) -> dict[str, Any]:
    """Counts per outcome and per reason, plus the labelling coverage.

    Coverage is reported deliberately: a summary that shows 6 confirmed frauds
    without saying they came from 12 labels out of 20,000 transactions invites
    the reader to mistake a sample for the population.
    """
    # Unpacked explicitly rather than dict(): mypy does not see a SQLAlchemy Row
    # as a 2-tuple.
    outcome_rows: dict[Any, int] = {  # noqa: C416 - dict() loses the Row typing
        outcome: count
        for outcome, count in session.execute(
            select(AnalystFeedback.outcome, func.count()).group_by(AnalystFeedback.outcome)
        ).all()
    }
    by_outcome = {str(key): value for key, value in outcome_rows.items()}

    reason_rows = session.execute(
        select(AnalystFeedback.reason_code, func.count())
        .group_by(AnalystFeedback.reason_code)
        .order_by(func.count().desc())
    ).all()

    total = sum(by_outcome.values())
    labelled = sum(
        count
        for outcome, count in outcome_rows.items()
        if FeedbackOutcome(str(outcome)).is_ground_truth
    )
    transactions = session.scalar(select(func.count(Transaction.id))) or 0
    reviewed = session.scalar(select(func.count(ReviewCase.id))) or 0

    return {
        "total_feedback": total,
        "confirmed_fraud": by_outcome.get(str(FeedbackOutcome.CONFIRMED_FRAUD), 0),
        "legitimate": by_outcome.get(str(FeedbackOutcome.LEGITIMATE), 0),
        "false_positive": by_outcome.get(str(FeedbackOutcome.FALSE_POSITIVE), 0),
        "false_negative": by_outcome.get(str(FeedbackOutcome.FALSE_NEGATIVE), 0),
        "insufficient_evidence": by_outcome.get(str(FeedbackOutcome.INSUFFICIENT_EVIDENCE), 0),
        "escalated": by_outcome.get(str(FeedbackOutcome.ESCALATED), 0),
        "ground_truth_labels": labelled,
        "total_transactions": transactions,
        "total_review_cases": reviewed,
        "labelled_share_of_transactions": (labelled / transactions) if transactions else 0.0,
        "by_reason": [
            {"reason_code": str(reason), "count": count} for reason, count in reason_rows
        ],
    }


#: The four machine actions, in precedence order, for a stable matrix layout.
_MACHINE_ACTIONS = (
    DecisionAction.APPROVE,
    DecisionAction.STEP_UP,
    DecisionAction.REVIEW,
    DecisionAction.BLOCK,
)


def confusion_matrix(session: Session) -> dict[str, Any]:
    """Machine decision against the analyst's ground-truth verdict.

    **Only ground-truth outcomes are included.** INSUFFICIENT_EVIDENCE and
    ESCALATED say the question is still open; placing them in either column
    would invent a label the analyst deliberately withheld. They are counted
    separately and reported as excluded.

    Unlabelled transactions do not appear at all. They are not negatives.
    """
    rows = session.execute(
        select(RiskDecision.action, AnalystFeedback.outcome, func.count())
        .select_from(AnalystFeedback)
        .join(RiskDecision, RiskDecision.id == AnalystFeedback.risk_decision_id)
        .group_by(RiskDecision.action, AnalystFeedback.outcome)
    ).all()

    cells: list[dict[str, Any]] = []
    excluded = 0
    included = 0
    for action, outcome, count in rows:
        parsed = FeedbackOutcome(str(outcome))
        if not parsed.is_ground_truth:
            excluded += count
            continue
        included += count
        cells.append(
            {
                "machine_decision": str(action).upper(),
                "outcome": str(outcome),
                "actually_fraud": parsed.indicates_fraud,
                "count": count,
            }
        )

    # A "positive" machine call is one that did not let the payment through
    # untouched: STEP_UP, REVIEW and BLOCK all express suspicion.
    true_positive = sum(
        cell["count"]
        for cell in cells
        if cell["actually_fraud"] and cell["machine_decision"] != "APPROVE"
    )
    false_negative = sum(
        cell["count"]
        for cell in cells
        if cell["actually_fraud"] and cell["machine_decision"] == "APPROVE"
    )
    false_positive = sum(
        cell["count"]
        for cell in cells
        if not cell["actually_fraud"] and cell["machine_decision"] != "APPROVE"
    )
    true_negative = sum(
        cell["count"]
        for cell in cells
        if not cell["actually_fraud"] and cell["machine_decision"] == "APPROVE"
    )

    return {
        "cells": cells,
        "machine_actions": [str(action).upper() for action in _MACHINE_ACTIONS],
        "true_positive": true_positive,
        "false_negative": false_negative,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "labelled_included": included,
        "excluded_open_outcomes": excluded,
    }


def load_by_public_id(session: Session, public_id: str) -> AnalystFeedback | None:
    return session.scalar(select(AnalystFeedback).where(AnalystFeedback.public_id == public_id))
