"""Scoring transactions and persisting the result.

The probability always comes from the trained model. Nothing in this module
adjusts, floors, caps or overrides it, and no transaction has a hardcoded score.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.metrics import observe_stage, risk_latency, risk_predictions_total
from app.core.observability import LifecycleEvent, log_lifecycle
from app.models import RiskPrediction as RiskPredictionRow
from app.models import Transaction
from ml.features.loader import to_view
from ml.inference.predictor import RiskPrediction, get_predictor

logger = logging.getLogger(__name__)

#: Matches the precision of ``risk_predictions.fraud_probability`` (NUMERIC(6,5)).
PROBABILITY_QUANTUM = Decimal("0.00001")


def score_transaction(session: Session, transaction: Transaction) -> RiskPrediction:
    """Build point-in-time features for a transaction and run the model."""
    predictor = get_predictor()
    return predictor.predict(session, to_view(transaction))


def store_prediction(
    session: Session, transaction: Transaction, prediction: RiskPrediction
) -> RiskPredictionRow:
    """Persist the prediction, replacing any earlier one for this transaction.

    ``risk_predictions.transaction_id`` is unique, so the table holds the latest
    score per transaction rather than an ever-growing history. Re-scoring after
    a retrain updates the row in place and stamps the new ``model_version`` and
    ``created_at``, which is the versioning that matters here: which model
    produced the score currently on record.
    """
    row = session.scalar(
        select(RiskPredictionRow).where(RiskPredictionRow.transaction_id == transaction.id)
    )

    probability = Decimal(str(prediction.fraud_probability)).quantize(PROBABILITY_QUANTUM)

    if row is None:
        row = RiskPredictionRow(
            transaction_id=transaction.id,
            fraud_probability=probability,
            risk_score=prediction.risk_score,
            model_version=prediction.model_version,
            created_at=datetime.now(UTC),
        )
        session.add(row)
    else:
        row.fraud_probability = probability
        row.risk_score = prediction.risk_score
        row.model_version = prediction.model_version
        row.created_at = datetime.now(UTC)

    session.flush()
    return row


def bulk_replace_predictions(
    session: Session,
    predictions: Sequence[tuple[int, RiskPrediction]],
) -> int:
    """Replace predictions for many transactions in two statements.

    Scoring the whole dataset row-by-row would mean a SELECT plus an
    INSERT/UPDATE per transaction - tens of thousands of round trips. Deleting
    the affected rows and bulk-inserting keeps it to two statements per chunk
    while preserving the "one current score per transaction" rule.
    """
    if not predictions:
        return 0

    transaction_ids = [transaction_id for transaction_id, _ in predictions]
    session.execute(
        delete(RiskPredictionRow).where(RiskPredictionRow.transaction_id.in_(transaction_ids))
    )

    created_at = datetime.now(UTC)
    session.execute(
        insert(RiskPredictionRow),
        [
            {
                "transaction_id": transaction_id,
                "fraud_probability": Decimal(str(prediction.fraud_probability)).quantize(
                    PROBABILITY_QUANTUM
                ),
                "risk_score": prediction.risk_score,
                "model_version": prediction.model_version,
                "created_at": created_at,
            }
            for transaction_id, prediction in predictions
        ],
    )
    session.flush()
    return len(predictions)


def predict_and_store(
    session: Session, transaction: Transaction
) -> tuple[RiskPrediction, RiskPredictionRow]:
    """Score a transaction and record the result.

    Instrumented here rather than in the callers so the batch path, the HTTP
    endpoint and the live pipeline all count the same way. A metric that only
    sees one of three entry points is worse than none: it reads as a drop in
    volume when work simply moved.
    """
    with observe_stage(risk_latency):
        prediction = score_transaction(session, transaction)
        row = store_prediction(session, transaction, prediction)

    risk_predictions_total.inc()
    log_lifecycle(
        LifecycleEvent.RISK_SCORED,
        transaction_id=transaction.transaction_id,
        fraud_probability=prediction.fraud_probability,
        risk_score=prediction.risk_score,
        model_version=prediction.model_version,
    )
    return prediction, row
