"""Behavioral anomaly scoring and risk-signal persistence.

Kept strictly separate from ``app.services.risk``: the supervised prediction and
the anomaly signal are two independent assessments, written to two different
tables, and neither overwrites or reads the other. Combining them is Phase 5.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.metrics import anomalies_total, anomaly_latency, observe_stage
from app.core.observability import LifecycleEvent, log_lifecycle
from app.models import RiskSignal, Transaction
from app.models.enums import SignalSeverity
from ml.anomaly.predictor import AnomalyResult, get_anomaly_predictor
from ml.features.loader import to_view

logger = logging.getLogger(__name__)

#: Signal names this service owns. Rescoring replaces only these.
ANOMALY_SIGNAL = "behavioral_anomaly"
CUSTOMER_DEVIATION_SIGNAL = "customer_relative_deviation"
OWNED_SIGNALS = (ANOMALY_SIGNAL, CUSTOMER_DEVIATION_SIGNAL)

#: ``risk_signals.signal_value`` is NUMERIC(16,4).
SIGNAL_QUANTUM = Decimal("0.0001")

#: The API speaks in uppercase severity bands; the column stores the Phase 2
#: enum, which is lowercase. One mapping, in one place.
SEVERITY_BY_NAME: dict[str, SignalSeverity] = {
    "LOW": SignalSeverity.LOW,
    "MEDIUM": SignalSeverity.MEDIUM,
    "HIGH": SignalSeverity.HIGH,
    "CRITICAL": SignalSeverity.CRITICAL,
}


def score_transaction(session: Session, transaction: Transaction) -> AnomalyResult:
    """Build point-in-time behavioral features and run the forest."""
    return get_anomaly_predictor().score(session, to_view(transaction))


def _signal_rows(
    transaction_id: int, result: AnomalyResult, at: datetime
) -> list[dict[str, object]]:
    severity = SEVERITY_BY_NAME[str(result.severity)]
    return [
        {
            "transaction_id": transaction_id,
            "signal_name": ANOMALY_SIGNAL,
            "signal_value": Decimal(result.anomaly_score).quantize(SIGNAL_QUANTUM),
            "severity": severity,
            "source": result.model_version,
            "created_at": at,
        },
        {
            "transaction_id": transaction_id,
            "signal_name": CUSTOMER_DEVIATION_SIGNAL,
            "signal_value": Decimal(result.customer_deviation_score).quantize(SIGNAL_QUANTUM),
            "severity": severity,
            "source": result.model_version,
            "created_at": at,
        },
    ]


def store_signals(
    session: Session, transaction: Transaction, result: AnomalyResult
) -> list[RiskSignal]:
    """Replace this transaction's anomaly signals with the latest assessment.

    ``risk_signals`` has no uniqueness constraint - it is designed to hold many
    signals per transaction from many sources - so idempotency is enforced by
    deleting only the rows this service owns before inserting.
    """
    session.execute(
        delete(RiskSignal).where(
            RiskSignal.transaction_id == transaction.id,
            RiskSignal.signal_name.in_(OWNED_SIGNALS),
        )
    )
    now = datetime.now(UTC)
    session.execute(insert(RiskSignal), _signal_rows(transaction.id, result, now))
    session.flush()

    return list(
        session.scalars(
            select(RiskSignal).where(
                RiskSignal.transaction_id == transaction.id,
                RiskSignal.signal_name.in_(OWNED_SIGNALS),
            )
        )
    )


def bulk_replace_signals(session: Session, results: Sequence[tuple[int, AnomalyResult]]) -> int:
    """Replace anomaly signals for many transactions in two statements.

    Same principle as the Phase 3 bulk path: scoring 20,000 transactions must
    not mean 40,000 round trips.
    """
    if not results:
        return 0

    transaction_ids = [transaction_id for transaction_id, _ in results]
    session.execute(
        delete(RiskSignal).where(
            RiskSignal.transaction_id.in_(transaction_ids),
            RiskSignal.signal_name.in_(OWNED_SIGNALS),
        )
    )

    now = datetime.now(UTC)
    payload: list[dict[str, object]] = []
    for transaction_id, result in results:
        payload.extend(_signal_rows(transaction_id, result, now))

    session.execute(insert(RiskSignal), payload)
    session.flush()
    return len(results)


def score_and_store(
    session: Session, transaction: Transaction
) -> tuple[AnomalyResult, list[RiskSignal]]:
    """Score a transaction's behaviour and record the resulting signals."""
    with observe_stage(anomaly_latency):
        result = score_transaction(session, transaction)
        rows = store_signals(session, transaction, result)

    anomalies_total.labels(severity=str(result.severity)).inc()
    log_lifecycle(
        LifecycleEvent.ANOMALY_SCORED,
        transaction_id=transaction.transaction_id,
        anomaly_score=result.anomaly_score,
        severity=str(result.severity),
        model_version=result.model_version,
    )
    return result, rows


__all__ = [
    "ANOMALY_SIGNAL",
    "CUSTOMER_DEVIATION_SIGNAL",
    "OWNED_SIGNALS",
    "bulk_replace_signals",
    "score_and_store",
    "score_transaction",
    "store_signals",
]
