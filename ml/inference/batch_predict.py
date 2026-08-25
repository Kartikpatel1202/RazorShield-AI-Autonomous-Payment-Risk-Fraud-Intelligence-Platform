"""Score many transactions and persist the results.

    python -m ml.inference.batch_predict --limit 500
    python -m ml.inference.batch_predict --all
    python -m ml.inference.batch_predict --transaction TXN_SCENARIO_B_CURRENT

Every probability comes from the trained model. No row is ever written with an
invented, defaulted or rule-derived score: if the model cannot score a
transaction the run fails rather than filling the table with placeholders.

For a whole-dataset run the features come from the streaming point-in-time
provider, which is one pass over the data rather than one query set per row.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models import Transaction
from app.services.risk import bulk_replace_predictions, store_prediction
from ml.features.batch_provider import iter_feature_rows
from ml.inference.predictor import FraudRiskPredictor, get_predictor

logger = logging.getLogger(__name__)

CHUNK_SIZE = 2_000


def _transaction_by_reference(session: Session, reference: str) -> Transaction:
    transaction = session.scalar(select(Transaction).where(Transaction.transaction_id == reference))
    if transaction is None:
        raise LookupError(f"transaction {reference!r} was not found")
    return transaction


def score_all(session: Session, predictor: FraudRiskPredictor, limit: int | None) -> int:
    """Score transactions in one chronological pass, in chunks.

    Features come from the streaming provider (one pass, no per-row queries),
    scoring is batched into single model calls, and each chunk is persisted with
    two statements rather than two per row.
    """
    scored = 0
    chunk: list[tuple[int, str, dict[str, Any]]] = []

    def flush() -> None:
        nonlocal chunk
        if not chunk:
            return
        predictions = predictor.predict_many(
            [(reference, features) for _, reference, features in chunk]
        )
        bulk_replace_predictions(
            session,
            [
                (db_id, prediction)
                for (db_id, _, _), prediction in zip(chunk, predictions, strict=True)
            ],
        )
        chunk = []

    for view, features in iter_feature_rows(session):
        if limit is not None and scored >= limit:
            break
        chunk.append((view.id, view.transaction_id, features))
        scored += 1

        if len(chunk) >= CHUNK_SIZE:
            flush()
            logger.info("  scored %d transactions", scored)

    flush()
    return scored


def score_one(session: Session, predictor: FraudRiskPredictor, reference: str) -> dict[str, Any]:
    transaction = _transaction_by_reference(session, reference)
    from ml.features.loader import to_view

    prediction = predictor.predict(session, to_view(transaction))
    store_prediction(session, transaction, prediction)
    return prediction.as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="score every transaction")
    group.add_argument("--limit", type=int, help="score the oldest N transactions")
    group.add_argument("--transaction", help="score a single transaction reference")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    predictor = get_predictor()
    logger.info("Scoring with model %s", predictor.model_version)

    started = time.perf_counter()
    with SessionLocal() as session:
        try:
            if args.transaction:
                result = score_one(session, predictor, args.transaction)
                logger.info(
                    "%s -> probability=%.5f risk_score=%d",
                    result["transaction_id"],
                    result["fraud_probability"],
                    result["risk_score"],
                )
                scored = 1
            else:
                scored = score_all(session, predictor, None if args.all else args.limit)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Batch scoring failed; no predictions were written")
            return 1

    elapsed = time.perf_counter() - started
    logger.info(
        "Scored and stored %d transaction(s) in %.2fs (%.1f/s)",
        scored,
        elapsed,
        scored / elapsed if elapsed else 0.0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
