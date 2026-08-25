"""Phase 3 vs Phase 4: the signal matrix.

    python -m ml.anomaly.compare

Cross-tabulates the supervised fraud probability against the unsupervised
anomaly score. It deliberately does **not** combine them into a single number -
that is the decision engine's job in a later phase, informed by Phase 5's
investigation.

The four quadrants each mean something different, and the point of this utility
is to show that they are populated by genuinely different transactions:

* **low / low** - ordinary traffic.
* **high fraud / high anomaly** - both engines agree; the strongest cases.
* **high fraud / low anomaly** - matches a learned fraud pattern while behaving
  unremarkably.
* **low fraud / high anomaly** - behaves strangely without matching any learned
  fraud pattern. This is the quadrant Phase 3 could not see, and the reason
  Phase 4 exists.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import numpy as np
import pandas as pd

from app.core.logging import configure_logging
from ml.anomaly.paths import SIGNAL_MATRIX_PATH
from ml.anomaly.predictor import BehavioralAnomalyPredictor
from ml.anomaly.train import MODEL_VERSION as ANOMALY_VERSION
from ml.config import METRICS_PATH, MODEL_PATH
from ml.features.schema import FEATURE_COLUMNS, TARGET_COLUMN
from ml.inference.predictor import FraudRiskPredictor
from ml.training.build_dataset import load_dataset
from ml.training.settings import load_config
from ml.training.split import split_chronologically

logger = logging.getLogger(__name__)

DEMO_SCENARIOS = (
    "TXN_SCENARIO_A_CURRENT",
    "TXN_SCENARIO_B_CURRENT",
    "TXN_SCENARIO_C_CURRENT_1",
    "TXN_SCENARIO_C_CURRENT_2",
    "TXN_SCENARIO_C_CURRENT_3",
)


def supervised_threshold() -> float:
    """The Phase 3 operating point, read from its metrics rather than re-chosen."""
    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    return float(metrics["models"]["xgboost-v1"]["threshold"]["threshold"])


def build_matrix(frame: pd.DataFrame) -> dict[str, Any]:
    """Score a set of transactions with both engines and cross-tabulate."""
    supervised = FraudRiskPredictor.load(MODEL_PATH)
    anomaly = BehavioralAnomalyPredictor.load()

    # The Phase 3 contract is strict about extra columns, so dataset metadata is
    # stripped before it is handed over. Phase 4 selects its own subset.
    rows = [
        (str(record["transaction_id"]), {name: record[name] for name in FEATURE_COLUMNS})
        for record in frame.to_dict(orient="records")
    ]

    fraud_probabilities = np.array(
        [p.fraud_probability for p in supervised.predict_many(rows)], dtype=float
    )
    anomaly_scores = np.array([r.anomaly_score for r in anomaly.score_many(rows)], dtype=float)

    fraud_threshold = supervised_threshold()
    anomaly_threshold = anomaly.threshold

    high_fraud = fraud_probabilities >= fraud_threshold
    high_anomaly = anomaly_scores >= anomaly_threshold
    labels = frame[TARGET_COLUMN].to_numpy(dtype=int)

    quadrants: dict[str, dict[str, Any]] = {}
    for name, mask in (
        ("low_fraud_low_anomaly", ~high_fraud & ~high_anomaly),
        ("low_fraud_high_anomaly", ~high_fraud & high_anomaly),
        ("high_fraud_low_anomaly", high_fraud & ~high_anomaly),
        ("high_fraud_high_anomaly", high_fraud & high_anomaly),
    ):
        count = int(mask.sum())
        fraud_rows = int(labels[mask].sum()) if count else 0
        quadrants[name] = {
            "transactions": count,
            "fraud_rows": fraud_rows,
            "fraud_rate": fraud_rows / count if count else 0.0,
        }

    is_fraud = labels == 1
    return {
        "rows": len(frame),
        "fraud_rows": int(labels.sum()),
        "supervised_model": "xgboost-v1",
        "supervised_threshold": fraud_threshold,
        "anomaly_model": ANOMALY_VERSION,
        "anomaly_threshold": anomaly_threshold,
        "quadrants": quadrants,
        "complementarity": {
            "fraud_caught_only_by_anomaly": int((~high_fraud & high_anomaly & is_fraud).sum()),
            "fraud_caught_only_by_supervised": int((high_fraud & ~high_anomaly & is_fraud).sum()),
            "fraud_caught_by_both": int((high_fraud & high_anomaly & is_fraud).sum()),
            "fraud_missed_by_both": int((~high_fraud & ~high_anomaly & is_fraud).sum()),
        },
        "rank_correlation": float(
            pd.Series(fraud_probabilities).corr(pd.Series(anomaly_scores), method="spearman")
        ),
    }


def demo_comparison(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Side-by-side signals for the deterministic demo scenarios."""
    supervised = FraudRiskPredictor.load(MODEL_PATH)
    anomaly = BehavioralAnomalyPredictor.load()

    rows: list[dict[str, Any]] = []
    for reference in DEMO_SCENARIOS:
        match = frame[frame["transaction_id"] == reference]
        if match.empty:
            continue
        record = dict(match.iloc[0].to_dict())
        model_input = {name: record[name] for name in FEATURE_COLUMNS}
        prediction = supervised.predict_from_features(reference, model_input)
        assessment = anomaly.score_from_features(reference, model_input)
        rows.append(
            {
                "transaction_id": reference,
                "is_fraud": int(record[TARGET_COLUMN]),
                "fraud_probability": prediction.fraud_probability,
                "risk_score": prediction.risk_score,
                "supervised_flags": prediction.exceeds_threshold,
                "anomaly_score": assessment.anomaly_score,
                "severity": str(assessment.severity),
                "anomaly_flags": assessment.exceeds_threshold,
                "customer_deviation_score": assessment.customer_deviation_score,
                "customer_deviation_driver": assessment.customer_deviation_driver,
                "top_deviations": [d.as_dict() for d in assessment.top_deviations[:5]],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    frame = load_dataset()
    split = split_chronologically(frame, load_config().split)

    payload: dict[str, Any] = {
        "test_fold": build_matrix(split.test.frame),
        "validation_fold": build_matrix(split.validation.frame),
        "demo_scenarios": demo_comparison(frame),
    }
    SIGNAL_MATRIX_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    matrix = payload["test_fold"]
    logger.info("Signal matrix on the test fold (%d rows):", matrix["rows"])
    for name, cell in matrix["quadrants"].items():
        logger.info(
            "  %-24s %5d transactions, %3d fraud (%.1f%%)",
            name,
            cell["transactions"],
            cell["fraud_rows"],
            100 * cell["fraud_rate"],
        )
    logger.info("Complementarity: %s", matrix["complementarity"])
    logger.info("Matrix -> %s", SIGNAL_MATRIX_PATH.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
