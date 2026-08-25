"""Assembling the decision context, and persisting what the policy decided.

This module is the only bridge between the database and the pure ``policy``
package. It has three jobs and no others:

1. **Read** the stored signals and turn them into a :class:`RiskContext`.
2. **Call** ``policy.engine.evaluate`` - which is a pure function.
3. **Write** the immutable decision row, the review case if one is required, and
   the audit entry.

Nothing here decides anything. If you want to know why a transaction was
blocked, read ``policy/rules.py``; this file only moves data.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.metrics import decision_latency, decisions_total, observe_stage
from app.core.observability import LifecycleEvent, log_lifecycle
from app.models import (
    AuditLog,
    Investigation,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.models.enums import ActorType, DecisionAction
from app.services.anomaly import ANOMALY_SIGNAL
from policy.actions import Action
from policy.context import (
    AnomalySignal,
    InvestigationSignal,
    RiskContext,
    SupervisedSignal,
    TransactionFacts,
)
from policy.engine import PolicyResult, evaluate
from policy.loader import get_policy
from policy.schema import PolicyConfig

logger = logging.getLogger(__name__)

DECISION_ACTOR = "risk-decision-engine"

#: The policy engine's vocabulary mapped onto the stored column. One-to-one and
#: exhaustive; a missing entry would be a KeyError rather than a silent default.
_ACTION_TO_COLUMN: dict[Action, DecisionAction] = {
    Action.APPROVE: DecisionAction.APPROVE,
    Action.STEP_UP: DecisionAction.STEP_UP,
    Action.REVIEW: DecisionAction.REVIEW,
    Action.BLOCK: DecisionAction.BLOCK,
}

#: Evidence at or above this severity counts as an independent corroborating
#: source. Mirrors the agent's own HIGH/CRITICAL banding.
_SERIOUS_SEVERITIES = frozenset({"HIGH", "CRITICAL"})

#: Distinct customers on one device or IP before a shared-entity concern is
#: recorded. Matches the tools that emit ``customer_count``.
_SHARED_ENTITY_MIN_CUSTOMERS = 2


def _supervised_signal(session: Session, transaction: Transaction) -> SupervisedSignal:
    prediction = session.scalar(
        select(RiskPrediction).where(RiskPrediction.transaction_id == transaction.id)
    )
    if prediction is None:
        return SupervisedSignal(available=False)
    return SupervisedSignal(
        available=True,
        fraud_probability=float(prediction.fraud_probability),
        risk_score=prediction.risk_score,
        model_version=prediction.model_version,
    )


def _anomaly_signal(session: Session, transaction: Transaction) -> AnomalySignal:
    signal = session.scalar(
        select(RiskSignal).where(
            RiskSignal.transaction_id == transaction.id,
            RiskSignal.signal_name == ANOMALY_SIGNAL,
        )
    )
    if signal is None:
        return AnomalySignal(available=False)
    return AnomalySignal(
        available=True,
        anomaly_score=int(signal.signal_value),
        severity=str(signal.severity).upper(),
        model_version=signal.source,
    )


def investigation_signal_from_report(
    *,
    status: str,
    confidence: float | None,
    public_id: str | None,
    report: dict[str, Any],
) -> InvestigationSignal:
    """Reduce a stored investigation to counts a rule may see.

    Everything extracted here is either a count of structured records or a
    number the application computed. The summary, the risk level and the
    recommended action are present in ``report`` and deliberately discarded - a
    rule has no field in which to receive them.

    Split out from the row lookup so the batch path in
    :mod:`app.services.decision_batch` reduces reports the same way rather than
    growing a second, drifting interpretation of the same document.
    """
    evidence: Sequence[dict[str, Any]] = report.get("evidence") or []
    findings: Sequence[dict[str, Any]] = report.get("findings") or []

    severity_counts: dict[str, int] = {}
    serious_sources: set[str] = set()
    all_sources: set[str] = set()
    shared_entity = False

    for item in evidence:
        severity = str(item.get("severity", "")).upper()
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        source = str(item.get("source_tool", ""))
        if source:
            all_sources.add(source)
            if severity in _SERIOUS_SEVERITIES:
                serious_sources.add(source)
        # ``customer_count`` is emitted only by the device and IP tools, and only
        # when one entity served several customers. Reading the structured key
        # rather than matching on claim text keeps this immune to wording.
        details = item.get("details") or {}
        count = details.get("customer_count")
        if isinstance(count, int | float) and count >= _SHARED_ENTITY_MIN_CUSTOMERS:
            shared_entity = True

    high_findings = sum(
        1 for finding in findings if str(finding.get("severity", "")).upper() in _SERIOUS_SEVERITIES
    )

    return InvestigationSignal(
        available=True,
        status=status,
        confidence=confidence,
        investigation_id=public_id,
        high_severity_findings=high_findings,
        independent_high_severity_sources=len(serious_sources),
        independent_evidence_sources=len(all_sources),
        evidence_severity_counts=severity_counts,
        shared_entity_observed=shared_entity,
    )


def _investigation_signal(session: Session, transaction: Transaction) -> InvestigationSignal:
    """The stored investigation for one transaction, reduced to rule-visible counts."""
    row = session.scalar(
        select(Investigation).where(Investigation.transaction_id == transaction.id)
    )
    if row is None:
        return InvestigationSignal(available=False)
    return investigation_signal_from_report(
        status=str(row.status),
        confidence=float(row.confidence) if row.confidence is not None else None,
        public_id=row.public_id,
        report=row.report or {},
    )


def build_context(session: Session, transaction: Transaction) -> RiskContext:
    """Gather every input the policy is allowed to consider."""
    return RiskContext(
        transaction=TransactionFacts(
            transaction_id=transaction.transaction_id,
            amount=float(transaction.amount),
            currency=transaction.currency,
            country=transaction.country or "",
            status=str(transaction.status),
            # Derived, not stored: the schema has no cross-border flag, so it is
            # computed from two recorded countries rather than left to default
            # to a value that would be wrong for every foreign payment.
            is_cross_border=transaction.country != transaction.customer.country,
        ),
        supervised=_supervised_signal(session, transaction),
        anomaly=_anomaly_signal(session, transaction),
        investigation=_investigation_signal(session, transaction),
    )


def decide(
    session: Session, transaction: Transaction, *, policy: PolicyConfig | None = None
) -> PolicyResult:
    """Evaluate the policy. Reads only - nothing is written."""
    return evaluate(build_context(session, transaction), policy or get_policy())


def timed_evaluate(context: RiskContext, policy: PolicyConfig) -> tuple[PolicyResult, float]:
    """Evaluate, and report how long the pure evaluation took in milliseconds.

    The timing is measured around ``evaluate`` alone - not the surrounding
    queries - so the number answers "how long does the policy take to decide?"
    rather than "how slow is the database today?".
    """
    started = time.perf_counter()
    result = evaluate(context, policy)
    return result, (time.perf_counter() - started) * 1000.0


def _decision_public_id() -> str:
    return f"DEC-{uuid.uuid4().hex[:16]}"


def store_decision(
    session: Session,
    transaction: Transaction,
    result: PolicyResult,
    policy: PolicyConfig,
    *,
    decided_at: datetime | None = None,
    evaluation_ms: float | None = None,
) -> RiskDecision:
    """Append the decision to the immutable history.

    Always an INSERT. Re-deciding a transaction adds a row; it never edits the
    previous one, so "what did we do, and when" stays answerable.
    """
    summary = result.risk_summary
    row = RiskDecision(
        public_id=_decision_public_id(),
        transaction_id=transaction.id,
        action=_ACTION_TO_COLUMN[result.action],
        policy_version=result.policy_version,
        decided_at=decided_at or datetime.now(UTC),
        matched_rules=list(result.matched_rules),
        reason_codes=list(result.reason_codes),
        explanation=result.explanation,
        requires_human_review=result.requires_human_review,
        fraud_probability=(
            Decimal(str(summary["fraud_probability"])).quantize(Decimal("0.00001"))
            if summary.get("fraud_probability") is not None
            else None
        ),
        risk_score=summary.get("risk_score"),
        fraud_model_version=summary.get("fraud_model_version"),
        anomaly_score=summary.get("anomaly_score"),
        anomaly_severity=summary.get("anomaly_severity"),
        anomaly_model_version=summary.get("anomaly_model_version"),
        investigation_public_id=summary.get("investigation_id"),
        investigation_confidence=(
            Decimal(str(summary["investigation_confidence"])).quantize(Decimal("0.0001"))
            if summary.get("investigation_confidence") is not None
            else None
        ),
        evaluation_ms=evaluation_ms,
        input_digest=result.input_digest,
        detail={**result.as_audit_record(), "policy": policy.as_dict()},
    )
    session.add(row)
    session.flush()
    return row


def write_audit_entry(
    session: Session, transaction: Transaction, result: PolicyResult, decision: RiskDecision
) -> AuditLog:
    """Record the decision on the transaction timeline.

    Identifiers, rules and reason codes only - the same discipline the
    investigation audit entry follows.
    """
    entry = AuditLog(
        transaction_id=transaction.id,
        actor_type=ActorType.SYSTEM,
        actor_id=DECISION_ACTOR,
        event_type="risk.decision",
        event_data={
            "decision_id": decision.public_id,
            "decision": str(result.action),
            "policy_version": result.policy_version,
            "matched_rules": list(result.matched_rules),
            "deciding_rules": list(result.deciding_rules),
            "reason_codes": list(result.reason_codes),
            "requires_human_review": result.requires_human_review,
            "input_digest": result.input_digest,
            "fraud_model_version": result.risk_summary.get("fraud_model_version"),
            "anomaly_model_version": result.risk_summary.get("anomaly_model_version"),
            "investigation_id": result.risk_summary.get("investigation_id"),
        },
    )
    session.add(entry)
    session.flush()
    return entry


def decide_and_store(
    session: Session, transaction: Transaction, *, policy: PolicyConfig | None = None
) -> tuple[PolicyResult, RiskDecision]:
    """Decide a transaction, persist the outcome, and open a review case if needed."""
    from app.services.review import open_case_for_decision  # circular at module scope

    active = policy or get_policy()
    with observe_stage(decision_latency):
        result, evaluation_ms = timed_evaluate(build_context(session, transaction), active)
        decision = store_decision(session, transaction, result, active, evaluation_ms=evaluation_ms)
        write_audit_entry(session, transaction, result, decision)

        if result.requires_human_review:
            open_case_for_decision(session, transaction, decision, result)

    decisions_total.labels(action=str(result.action).lower()).inc()
    log_lifecycle(
        LifecycleEvent.DECISION_CREATED,
        transaction_id=transaction.transaction_id,
        decision_id=decision.public_id,
        action=str(result.action),
        policy_version=result.policy_version,
        matched_rules=list(result.matched_rules),
        reason_codes=list(result.reason_codes),
        requires_human_review=result.requires_human_review,
        evaluation_ms=evaluation_ms,
    )
    return result, decision


def load_by_public_id(session: Session, public_id: str) -> RiskDecision | None:
    return session.scalar(select(RiskDecision).where(RiskDecision.public_id == public_id))


def load_history(session: Session, transaction: Transaction) -> list[RiskDecision]:
    """Every decision made about a transaction, oldest first."""
    return list(
        session.scalars(
            select(RiskDecision)
            .where(RiskDecision.transaction_id == transaction.id)
            .order_by(RiskDecision.decided_at, RiskDecision.id)
        )
    )
