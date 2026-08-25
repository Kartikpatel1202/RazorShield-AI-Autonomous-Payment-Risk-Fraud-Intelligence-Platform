"""Score many transactions' behaviour and persist the signals.

    python -m ml.anomaly.batch_predict --all
    python -m ml.anomaly.batch_predict --limit 500
    python -m ml.anomaly.batch_predict --transaction TXN_SCENARIO_C_CURRENT_1

Follows the Phase 3 batch principles: features come from the streaming
point-in-time provider (one pass, no per-row queries), scoring is batched into
single forest calls, and each chunk is persisted with two statements rather than
two per row.
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
from app.services.anomaly import bulk_replace_signals, store_signals
from ml.anomaly.predictor import BehavioralAnomalyPredictor, get_anomaly_predictor
from ml.features.batch_provider import iter_feature_rows
from ml.features.loader import to_view

logger = logging.getLogger(__name__)

CHUNK_SIZE = 2_000


def score_all(session: Session, predictor: BehavioralAnomalyPredictor, limit: int | None) -> int:
    """Score transactions in one chronological pass, in chunks."""
    scored = 0
    chunk: list[tuple[int, str, dict[str, Any]]] = []

    def flush() -> None:
        nonlocal chunk
        if not chunk:
            return
        results = predictor.score_many(
            [(reference, features) for _, reference, features in chunk], explain=False
        )
        bulk_replace_signals(
            session,
            [(db_id, result) for (db_id, _, _), result in zip(chunk, results, strict=True)],
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


def score_one(
    session: Session, predictor: BehavioralAnomalyPredictor, reference: str
) -> dict[str, Any]:
    transaction = session.scalar(select(Transaction).where(Transaction.transaction_id == reference))
    if transaction is None:
        raise LookupError(f"transaction {reference!r} was not found")

    result = predictor.score(session, to_view(transaction))
    store_signals(session, transaction, result)
    return result.as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="score every transaction")
    group.add_argument("--limit", type=int, help="score the oldest N transactions")
    group.add_argument("--transaction", help="score a single transaction reference")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    predictor = get_anomaly_predictor()
    logger.info("Scoring behaviour with model %s", predictor.model_version)

    started = time.perf_counter()
    with SessionLocal() as session:
        try:
            if args.transaction:
                result = score_one(session, predictor, args.transaction)
                logger.info(
                    "%s -> anomaly_score=%d severity=%s",
                    result["transaction_id"],
                    result["anomaly_score"],
                    result["severity"],
                )
                scored = 1
            else:
                scored = score_all(session, predictor, None if args.all else args.limit)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Batch anomaly scoring failed; no signals were written")
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
