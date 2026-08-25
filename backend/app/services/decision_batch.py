"""Deciding many transactions at once.

The per-transaction path in :mod:`app.services.decision` issues three SELECTs
and two INSERTs per transaction. That is right for one payment and hopeless for
twenty thousand: ~60,000 round trips.

This module loads every signal in three bulk queries, evaluates the *same* pure
policy engine in memory, and writes with bulk inserts. The decisions are
identical - the engine is a pure function of the context, and the context is
assembled from the same rows - only the I/O pattern differs.

It deliberately shares :func:`policy.engine.evaluate` rather than reimplementing
any rule. A batch path that made its own decisions would be a second policy.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Table, bindparam, insert, select, update
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Investigation,
    ReviewCase,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.models.enums import ActorType, ReviewCaseStatus
from app.services.anomaly import ANOMALY_SIGNAL
from app.services.decision import (
    _ACTION_TO_COLUMN,
    DECISION_ACTOR,
    investigation_signal_from_report,
)
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

#: Rows per flush. Large enough to amortise round trips, small enough that the
#: parameter list stays well inside PostgreSQL's limit.
CHUNK_SIZE = 1_000


@dataclass(frozen=True)
class BatchOutcome:
    """What a batch run produced."""

    decided: int
    review_cases_opened: int
    counts_by_action: dict[str, int]
    elapsed_seconds: float

    def summary(self) -> str:
        breakdown = ", ".join(
            f"{action}={count}" for action, count in sorted(self.counts_by_action.items())
        )
        return (
            f"{self.decided} decisions in {self.elapsed_seconds:.1f}s "
            f"({breakdown}); {self.review_cases_opened} review cases"
        )


def _supervised_by_transaction(session: Session) -> dict[int, SupervisedSignal]:
    rows = session.execute(
        select(
            RiskPrediction.transaction_id,
            RiskPrediction.fraud_probability,
            RiskPrediction.risk_score,
            RiskPrediction.model_version,
        )
    )
    return {
        transaction_id: SupervisedSignal(
            available=True,
            fraud_probability=float(probability),
            risk_score=risk_score,
            model_version=version,
        )
        for transaction_id, probability, risk_score, version in rows
    }


def _anomaly_by_transaction(session: Session) -> dict[int, AnomalySignal]:
    rows = session.execute(
        select(
            RiskSignal.transaction_id,
            RiskSignal.signal_value,
            RiskSignal.severity,
            RiskSignal.source,
        ).where(RiskSignal.signal_name == ANOMALY_SIGNAL)
    )
    return {
        transaction_id: AnomalySignal(
            available=True,
            anomaly_score=int(value),
            severity=str(severity).upper(),
            model_version=source,
        )
        for transaction_id, value, severity, source in rows
    }


def _investigation_by_transaction(session: Session) -> dict[int, InvestigationSignal]:
    rows = session.execute(
        select(
            Investigation.transaction_id,
            Investigation.status,
            Investigation.confidence,
            Investigation.public_id,
            Investigation.report,
        )
    )
    return {
        transaction_id: investigation_signal_from_report(
            status=str(status),
            confidence=float(confidence) if confidence is not None else None,
            public_id=public_id,
            report=report or {},
        )
        for transaction_id, status, confidence, public_id, report in rows
    }


def _transactions(session: Session, limit: int | None) -> Sequence[Any]:
    statement = select(
        Transaction.id,
        Transaction.transaction_id,
        Transaction.amount,
        Transaction.currency,
        Transaction.country,
        Transaction.status,
        Transaction.transaction_timestamp,
    ).order_by(Transaction.transaction_timestamp, Transaction.id)
    if limit is not None:
        statement = statement.limit(limit)
    return session.execute(statement).all()


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def decide_all(
    session: Session,
    *,
    policy: PolicyConfig | None = None,
    limit: int | None = None,
    open_review_cases: bool = True,
) -> BatchOutcome:
    """Decide every transaction that has at least one stored signal.

    ``decided_at`` is set to the transaction's own timestamp rather than wall
    clock. These are backfilled decisions over historical payments; stamping
    them all with "now" would make every time-series chart a single spike and
    would misrepresent when the decision applied.
    """
    started = datetime.now(UTC)
    active = policy or get_policy()

    supervised = _supervised_by_transaction(session)
    anomaly = _anomaly_by_transaction(session)
    investigations = _investigation_by_transaction(session)
    rows = _transactions(session, limit)

    decision_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for (
        internal_id,
        reference,
        amount,
        currency,
        country,
        status,
        timestamp,
    ) in rows:
        context = RiskContext(
            transaction=TransactionFacts(
                transaction_id=reference,
                amount=float(amount),
                currency=currency,
                country=country or "",
                status=str(status),
            ),
            supervised=supervised.get(internal_id, SupervisedSignal()),
            anomaly=anomaly.get(internal_id, AnomalySignal()),
            investigation=investigations.get(internal_id, InvestigationSignal()),
        )
        started_eval = time.perf_counter()
        result = evaluate(context, active)
        evaluation_ms = (time.perf_counter() - started_eval) * 1000.0
        action = str(result.action)
        counts[action] = counts.get(action, 0) + 1

        public_id = f"DEC-{uuid.uuid4().hex[:16]}"
        decision_rows.append(
            _decision_row(internal_id, public_id, result, active, timestamp, evaluation_ms)
        )
        audit_rows.append(_audit_row(internal_id, public_id, result, timestamp))
        if open_review_cases and result.requires_human_review:
            review_rows.append(
                {
                    "transaction_id": internal_id,
                    "status": ReviewCaseStatus.OPEN,
                    "reason": _case_reason(result),
                    "created_at": timestamp,
                }
            )

    for chunk in _chunks(decision_rows, CHUNK_SIZE):
        session.execute(insert(RiskDecision), chunk)
    for chunk in _chunks(audit_rows, CHUNK_SIZE):
        session.execute(insert(AuditLog), chunk)

    opened = _link_review_cases(session, review_rows) if review_rows else 0

    elapsed = (datetime.now(UTC) - started).total_seconds()
    outcome = BatchOutcome(
        decided=len(decision_rows),
        review_cases_opened=opened,
        counts_by_action=counts,
        elapsed_seconds=elapsed,
    )
    logger.info("Batch decision complete: %s", outcome.summary())
    return outcome


def _case_reason(result: PolicyResult) -> str:
    return f"{result.action}: {', '.join(result.deciding_rules) or 'policy default'}"[:255]


def _decision_row(
    internal_id: int,
    public_id: str,
    result: PolicyResult,
    policy: PolicyConfig,
    decided_at: datetime,
    evaluation_ms: float,
) -> dict[str, Any]:
    summary = result.risk_summary
    probability = summary.get("fraud_probability")
    confidence = summary.get("investigation_confidence")
    return {
        "public_id": public_id,
        "transaction_id": internal_id,
        "action": _ACTION_TO_COLUMN[result.action],
        "policy_version": result.policy_version,
        "decided_at": decided_at,
        "matched_rules": list(result.matched_rules),
        "reason_codes": list(result.reason_codes),
        "explanation": result.explanation,
        "requires_human_review": result.requires_human_review,
        "fraud_probability": (
            Decimal(str(probability)).quantize(Decimal("0.00001"))
            if probability is not None
            else None
        ),
        "risk_score": summary.get("risk_score"),
        "fraud_model_version": summary.get("fraud_model_version"),
        "anomaly_score": summary.get("anomaly_score"),
        "anomaly_severity": summary.get("anomaly_severity"),
        "anomaly_model_version": summary.get("anomaly_model_version"),
        "investigation_public_id": summary.get("investigation_id"),
        "investigation_confidence": (
            Decimal(str(confidence)).quantize(Decimal("0.0001")) if confidence is not None else None
        ),
        "evaluation_ms": evaluation_ms,
        "input_digest": result.input_digest,
        "detail": {**result.as_audit_record(), "policy": policy.as_dict()},
        "created_at": decided_at,
    }


def _audit_row(
    internal_id: int, public_id: str, result: PolicyResult, decided_at: datetime
) -> dict[str, Any]:
    return {
        "transaction_id": internal_id,
        "actor_type": ActorType.SYSTEM,
        "actor_id": DECISION_ACTOR,
        "event_type": "risk.decision",
        "event_data": {
            "decision_id": public_id,
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
        "created_at": decided_at,
    }


def _link_review_cases(session: Session, review_rows: list[dict[str, Any]]) -> int:
    """Insert review cases, then point each at the decision that opened it.

    Two steps because the decision ids are assigned by the database during the
    bulk insert above; a second pass reads them back rather than round-tripping
    each row individually.
    """
    for chunk in _chunks(review_rows, CHUNK_SIZE):
        session.execute(insert(ReviewCase), chunk)
    session.flush()

    # Ruff suggests dict(); mypy rejects it, because a SQLAlchemy Row is not
    # seen as a 2-tuple. Unpacking explicitly is what makes this type-check.
    decision_ids: dict[int, int] = {  # noqa: C416
        transaction_id: decision_id
        for transaction_id, decision_id in session.execute(
            select(RiskDecision.transaction_id, RiskDecision.id).order_by(RiskDecision.id)
        ).all()
    }
    cases = session.execute(
        select(ReviewCase.id, ReviewCase.transaction_id).where(
            ReviewCase.risk_decision_id.is_(None)
        )
    ).all()

    updates = [
        {"case_id": case_id, "decision_id": decision_ids[transaction_id]}
        for case_id, transaction_id in cases
        if transaction_id in decision_ids
    ]
    # A Core update against the table, not an ORM update against the entity.
    # The ORM form reads a list of dicts as "bulk update by primary key" and
    # demands an ``id`` in every row; this is a parameterised statement executed
    # once per chunk, which is a different thing.
    # ``__table__`` is declared as FromClause on the declarative base; the
    # runtime object is a Table, which is what ``update()`` needs.
    review_table = cast(Table, ReviewCase.__table__)
    statement = (
        update(review_table)
        .where(review_table.c.id == bindparam("case_id"))
        .values(risk_decision_id=bindparam("decision_id"))
    )
    for chunk in _chunks(updates, CHUNK_SIZE):
        session.execute(statement, chunk)
    # The identity map may now hold stale ReviewCase rows; drop them so any
    # later read sees the link that was just written.
    session.expire_all()
    return len(review_rows)
